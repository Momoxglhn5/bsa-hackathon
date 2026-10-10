"""Verification report must fail closed without leaking provider credentials."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('verify_sources', Path(__file__).resolve().parents[2] / 'scripts/verify_sources.py')
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


@pytest.fixture
def setup_report(monkeypatch, morpho_item, pendle_item):
    from app.ingestion.normalize import SourceRef, OnchainMarketParams, OnchainPendleTokens
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(days=30)
    expiry = expiry.replace(microsecond=0)
    pendle_item['expiry'] = expiry.isoformat()
    ref = SourceRef('ethereum_rpc', 'ev_' + 'a' * 24, now)
    raw = SimpleNamespace(source_id='test_api', evidence_id='ev_' + 'b' * 24, retrieved_at=now)
    def client(item):
        class Client:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def list_markets(self, **kwargs):
                return SimpleNamespace(pages=(1,), errors=(), complete=True,
                                       iter_items=lambda: iter([(item, raw)]))
        return Client
    monkeypatch.setattr(script, 'MorphoApiClient', client(morpho_item))
    monkeypatch.setattr(script, 'PendleApiClient', client(pendle_item))
    rpc = SimpleNamespace(chain_id=1, evidence={}, pin_block=lambda: SimpleNamespace(
        number=123, hash='0x' + 'ab' * 32, timestamp=now))
    monkeypatch.setattr(script, 'EthereumRpcClient', lambda **kwargs: rpc)
    params = OnchainMarketParams(morpho_item['market_id'], morpho_item['loan_token'],
        morpho_item['collateral_token'], morpho_item['oracle_address'], morpho_item['irm_address'],
        int(morpho_item['lltv_wad']), 123, ref)
    tokens = OnchainPendleTokens(pendle_item['address'], pendle_item['sy'][2:],
        pendle_item['pt'][2:], pendle_item['yt'][2:], int(expiry.timestamp()), 123, ref)
    monkeypatch.setattr(script, 'read_morpho_market_params', lambda *args: params)
    monkeypatch.setattr(script, 'read_pendle_market_tokens', lambda *args: (tokens, raw))
    manifest = dict(chainId=1, morphoMarketId=morpho_item['market_id'],
        pendleMarketAddress=pendle_item['address'], ptTokenAddress=pendle_item['pt'][2:],
        loanTokenAddress=morpho_item['loan_token'], oracleAddress=morpho_item['oracle_address'],
        expiry=expiry.isoformat())
    return manifest, raw, rpc


def test_complete_report(setup_report):
    report = script.verify(setup_report[0])
    assert report['ok'] and report['evidence_ids'] and report['block']['number'] == 123
    assert {'pt_matches_collateral', 'expiry.valid', 'oracle.recorded'} <= {
        c['name'] for c in report['checks']}


@pytest.mark.parametrize('field,value', [('oracleAddress', '0x' + '99' * 20),
    ('ptTokenAddress', '0x' + '99' * 20), ('expiry', '2020-01-01T00:00:00Z'), ('chainId', 8453)])
def test_manifest_mismatch_fails(setup_report, field, value):
    manifest = dict(setup_report[0], **{field: value})
    assert not script.verify(manifest)['ok']


def test_stale_api_fails(setup_report):
    manifest, raw, _ = setup_report
    raw.retrieved_at -= timedelta(minutes=10)
    report = script.verify(manifest)
    assert not report['ok']
    assert any(c['name'] == 'morpho.freshness' and c['status'] == 'FAIL' for c in report['checks'])


def test_rpc_failure_keeps_api_report_and_hides_secret(setup_report, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError('https://secret.example/private-key')
    monkeypatch.setattr(script, 'EthereumRpcClient', fail)
    report = script.verify(setup_report[0])
    assert not report['ok'] and 'private-key' not in str(report)
    assert any(c['name'] == 'pendle.market_exists' and c['status'] == 'PASS' for c in report['checks'])


def test_cli_failure_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(script.logging, 'disable', lambda *args: None)
    assert script.main(['--manifest', str(tmp_path / 'absent.json'), '--json']) == 1
    assert '"ok": false' in capsys.readouterr().out
