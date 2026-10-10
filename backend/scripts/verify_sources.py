"""Read-only verification of the REAL strategy selected for the demo.

Run from the repository root: .venv/bin/python backend/scripts/verify_sources.py
Or from backend/: ../.venv/bin/python scripts/verify_sources.py
Exit 1 on any failed critical check; --json emits a machine-readable report.
No manifest or database is modified, and no transaction is signed.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.adapters.ethereum_rpc import (
    EthereumRpcClient, read_morpho_market_params, read_pendle_market_tokens,
    verify_pt_matches_collateral,
)
from app.adapters.morpho_api import MorphoApiClient
from app.adapters.pendle_api import PendleApiClient
from app.ingestion.normalize import (
    SourceRef, morpho_param_checks, pendle_token_checks, parse_iso_utc,
)
from app.ingestion.verify import find_listing_item
from app.models.evidence import normalize_address, normalize_bytes32, ZERO_ADDRESS

MANIFEST = BACKEND / 'app' / 'fixtures' / 'demo_strategy_manifest.json'


def verify(manifest, *, max_age=300):
    report = {'data_mode': 'REAL', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'checks': [], 'evidence_ids': [], 'block': None}
    def add(name, ok, detail, evidence=()):
        report['checks'].append({'name': name, 'status': 'PASS' if ok else 'FAIL', 'detail': detail})
        report['evidence_ids'].extend(evidence)
    def attempt(name, fn):
        try:
            return fn()
        except Exception as exc:
            # Provider exceptions can contain credential-bearing URLs; report type only.
            add(name, False, f'{type(exc).__name__}; check provider configuration/availability')
            return None
    def age(name, timestamp):
        seconds = (datetime.now(timezone.utc) - timestamp).total_seconds()
        add(name, 0 <= seconds <= max_age,
            {'observed_at': timestamp.isoformat(), 'age_seconds': round(seconds, 2),
             'ttl_seconds': max_age})
    try:
        if manifest.get('chainId') != 1 or manifest.get('data_mode', 'REAL') != 'REAL':
            raise ValueError('requires REAL Ethereum mainnet manifest')
        market = normalize_bytes32(manifest['morphoMarketId'])
        pendle_address = normalize_address(manifest['pendleMarketAddress'])
        pt = normalize_address(manifest['ptTokenAddress'])
        loan = normalize_address(manifest['loanTokenAddress'])
        oracle = normalize_address(manifest['oracleAddress'])
        expiry = parse_iso_utc(manifest['expiry'])
    except (ValueError, KeyError, TypeError):
        add('manifest', False, 'Missing or invalid mainnet strategy identifiers/expiry')
        report['ok'] = False
        return report
    add('manifest', True, {'morpho_market_id': market, 'pendle_market': pendle_address})

    def fetch_morpho():
        with MorphoApiClient(timeout_s=10, max_attempts=2) as client:
            return client.list_markets(limit=100, max_pages=10, collateral_token=pt)
    def fetch_pendle():
        with PendleApiClient(timeout_s=10, max_attempts=2) as client:
            # Include expired markets so expiry failure is reported explicitly.
            return client.list_markets(limit=100, max_pages=30, is_active=None)
    found = {}
    for provider, fetch, field, identifier in (
        ('morpho', fetch_morpho, 'market_id', market),
        ('pendle', fetch_pendle, 'address', pendle_address),
    ):
        listing = attempt(f'{provider}.api', fetch)
        if listing is None:
            continue
        add(f'{provider}.api', bool(listing.pages),
            {'pages': len(listing.pages), 'complete': listing.complete, 'errors': len(listing.errors)})
        match = find_listing_item(listing, field, identifier)
        add(f'{provider}.market_exists', match is not None,
            identifier if match else 'Target absent from retrieved pages (possibly incomplete listing)')
        if match:
            found[provider] = match
            raw = match[1]
            report['evidence_ids'].append(raw.evidence_id)
            age(f'{provider}.freshness', raw.retrieved_at)

    rpc = attempt('rpc.chain_id', lambda: EthereumRpcClient(timeout_s=10, max_attempts=2))
    if rpc is not None:
        add('rpc.chain_id', rpc.chain_id == 1, rpc.chain_id)
        pinned = attempt('rpc.block', rpc.pin_block)
        if pinned is not None:
            report['block'] = {'number': pinned.number, 'hash': pinned.hash,
                               'timestamp': pinned.timestamp.isoformat()}
            age('rpc.freshness', pinned.timestamp)
            params = attempt('morpho.idToMarketParams', lambda: read_morpho_market_params(rpc, market, pinned))
            tokens_result = attempt('pendle.readTokens', lambda: read_pendle_market_tokens(rpc, pendle_address, pinned))
            if params is not None:
                add('morpho.idToMarketParams', True, market, [params.ref.evidence_id])
                add('manifest.collateral', params.collateral_token.lower() == pt, params.collateral_token)
                add('manifest.loan', params.loan_token.lower() == loan, params.loan_token)
                add('oracle.recorded', params.oracle.lower() == oracle and oracle != ZERO_ADDRESS, params.oracle)
                if 'morpho' in found:
                    item, raw = found['morpho']
                    for check in morpho_param_checks(item, SourceRef.from_response(raw), params):
                        add(check.check, check.status.value == 'VERIFIED', check.model_dump(mode='json'), check.evidence_ids)
            if tokens_result is not None:
                tokens, expiry_ev = tokens_result
                add('pendle.readTokens', True, {'pt': tokens.pt, 'sy': tokens.sy, 'yt': tokens.yt},
                    [tokens.ref.evidence_id, expiry_ev.evidence_id])
                add('manifest.pt', tokens.pt.lower() == pt, tokens.pt)
                onchain_expiry = datetime.fromtimestamp(tokens.expiry_ts, timezone.utc)
                add('expiry.valid', onchain_expiry == expiry and onchain_expiry > datetime.now(timezone.utc),
                    {'onchain': onchain_expiry.isoformat(), 'manifest': expiry.isoformat()})
                if 'pendle' in found:
                    item, raw = found['pendle']
                    for check in pendle_token_checks(item, SourceRef.from_response(raw), tokens):
                        add(check.check, check.status.value == 'VERIFIED', check.model_dump(mode='json'), check.evidence_ids)
                if params is not None:
                    check = verify_pt_matches_collateral(params, tokens)
                    add('pt_matches_collateral', check.status.value == 'VERIFIED',
                        check.model_dump(mode='json'), check.evidence_ids)
        report['evidence_ids'].extend(rpc.evidence)
    report['evidence_ids'] = list(dict.fromkeys(report['evidence_ids']))
    report['ok'] = all(c['status'] == 'PASS' for c in report['checks'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=MANIFEST)
    parser.add_argument('--max-age-seconds', type=int, default=300)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if args.max_age_seconds <= 0:
        parser.error('--max-age-seconds must be positive')
    # Avoid logging raw provider messages that may contain configured credentials.
    logging.disable(logging.CRITICAL)
    try:
        manifest = json.loads(args.manifest.read_text())
        if not isinstance(manifest, dict):
            raise ValueError('manifest must be an object')
    except (OSError, ValueError):
        report = {'ok': False, 'checks': [{'name': 'manifest', 'status': 'FAIL',
                  'detail': 'Cannot read a JSON manifest object'}], 'evidence_ids': []}
    else:
        report = verify(manifest, max_age=args.max_age_seconds)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print('REAL strategy source verification (read-only)')
        for check in report['checks']:
            print(f"[{check['status']}] {check['name']}: {check['detail']}")
        print('Pinned block:', report.get('block'))
        print('Evidence IDs:', ', '.join(report['evidence_ids']))
        print('RESULT:', 'PASS' if report['ok'] else 'FAIL')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
