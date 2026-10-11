"""Archive optional dependency reads at the financial replay's original block."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.adapters.ethereum_rpc import EthereumRpcClient, read_sy_asset_info
from app.analytics.dataset import load_snapshot
from app.ingestion.dependencies import discover_sy_dependencies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    snapshot = load_snapshot(args.snapshot)
    market = next(m for m in snapshot.markets if m.pendle)
    if market.data_mode.value != 'REAL' or market.block_number is None:
        raise ValueError('REAL Pendle market at a known block required')
    rpc = EthereumRpcClient()
    pinned = rpc.pin_block(market.block_number)
    info = read_sy_asset_info(rpc, market.pendle.sy.address, pinned)
    discovery = discover_sy_dependencies(rpc, info, pinned)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'schema_version': 'dependency-discovery-v1',
        'as_of': datetime.now(timezone.utc).isoformat(), 'block_number': pinned.number,
        'block_hash': pinned.hash, 'block_time': pinned.timestamp.isoformat(),
        'sy_address': info.sy_address, 'yield_token': info.yield_token,
        'relations': [r.model_dump(mode='json') for r in discovery.relations],
        'evidence': [e.model_dump(mode='json') for e in rpc.evidence.values()],
        'raw_returns': discovery.raw_returns, 'warnings': discovery.warnings,
        'limitations': ['Getter declarations do not establish reserves, solvency or mandatory risk propagation.',
                       'Read later at the original replay block; financial quotes remain dated.']}, indent=2)+'\n')
    print(f'Archived {len(discovery.relations)} declared links at block {pinned.number}; backing remains unverified')


if __name__ == '__main__':
    main()
