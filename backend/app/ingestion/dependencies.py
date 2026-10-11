"""Bounded optional interface discovery. Never equates getters with backing."""
from dataclasses import dataclass, field

from app.adapters.ethereum_rpc import EthereumRPCError
from app.models.evidence import ZERO_ADDRESS, RelationStatus, RelationType, make_relation, normalize_address


def getter(name, output):
    return {'type': 'function', 'name': name, 'stateMutability': 'view',
            'inputs': [], 'outputs': [{'name': 'result', 'type': output}]}


@dataclass
class DependencyDiscovery:
    relations: list = field(default_factory=list)
    raw_returns: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def discover_sy_dependencies(rpc, sy_info, pinned, *, max_depth=2):
    if sy_info.block_number != pinned.number or not 0 <= max_depth <= 3:
        raise ValueError('matching pinned block and bounded depth required')
    result = DependencyDiscovery()

    def read(address, method, output):
        try:
            call = rpc.call(address, getter(method, output), method, [], pinned)
        except EthereumRPCError as exc:
            # Optional interface absent or temporarily unreadable; retain its
            # error evidence in the RPC client, never invent a target.
            result.warnings.append(f'{address}: {method} unavailable ({type(exc).__name__})')
            return None
        result.raw_returns[call.evidence.evidence_id] = call.raw_return
        return call

    def link(address, target, relation, call, note):
        if target == ZERO_ADDRESS or target == address:
            result.warnings.append(f'{address}: zero/native or self target excluded')
            return
        result.relations.append(make_relation(source_node_id=f'asset:1:{address}',
            target_node_id=f'asset:1:{target}', relation_type=relation,
            verification_status=RelationStatus.ONCHAIN_VERIFIED, evidence=[call.evidence],
            observed_at=pinned.timestamp, note=note))

    sy = normalize_address(sy_info.sy_address)
    for method, relation in (('getTokensIn', RelationType.SY_ACCEPTS_INPUT),
                             ('getTokensOut', RelationType.SY_REDEEMS_TO)):
        call = read(sy, method, 'address[]')
        if call is None:
            continue
        if len(call.value) > 32:
            result.warnings.append(f'{method}: excessive token list excluded')
            continue
        for token in sorted({normalize_address(a) for a in call.value}):
            link(sy, token, relation, call,
                 'Declared alternative deposit/redemption token; not a guarantee of liquidity, execution or backing.')
    current = normalize_address(sy_info.yield_token)
    seen = {sy}
    for _ in range(max_depth):
        if current in seen or current == ZERO_ADDRESS:
            result.warnings.append('Asset traversal stopped: cycle or zero target')
            break
        seen.add(current)
        call = read(current, 'asset', 'address')
        if call is None:
            break
        target = normalize_address(call.value)
        if target in seen:
            result.warnings.append('Asset traversal stopped: self reference or cycle')
            break
        link(current, target, RelationType.TOKEN_DECLARED_ASSET, call,
             'asset() declares a token dependency; ERC4626 compliance, reserves and solvency remain unverified. No automatic loss propagation.')
        current = target
    return result
