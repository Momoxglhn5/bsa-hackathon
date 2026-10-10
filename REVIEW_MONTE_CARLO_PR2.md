# Demande de revue — Monte Carlo PR2 (DeFi Risk Radar, BSA Hackathon)

Tu es relecteur externe. Tu n'as pas accès au dépôt : tout le contexte nécessaire et le code clé sont dans ce document. Merci de chercher des **erreurs réelles** (calcul, logique, sécurité des données, affirmations trompeuses) plutôt que des préférences de style, et de dire explicitement quand tu n'es pas sûr.

## 1. Contexte du projet

- Produit : analyse en lecture seule de positions d'emprunt **Morpho Blue** utilisant des **PT Pendle** comme collatéral (Ethereum mainnet uniquement).
- Stack : Python 3.13, FastAPI, Pydantic v2, Decimal aux frontières, NumPy pour la simulation, SQLite + Parquet comme stores, aucun ML dans ce module.
- DTO partagés existants (contrat Data, non modifiés) : `PositionDTO`, `MarketDTO`, `StrategyDTO`, `OraclePriceDTO`, `TimeseriesDTO`, `EvidenceDTO`.
- **PR1** (déjà livré) : simulation GBM du **prix oracle Morpho** (loan token par PT) sur 1, 3 ou 7 jours, volatilité **supposée** par l'utilisateur, sortie `HYPOTHETICAL`. Métriques : LTV et health factor initiaux, chute oracle critique, fréquence de franchissement de la LLTV avec intervalle de Wilson, quantiles de LTV, VaR/ES 95 en unités de loan token. `realized_liquidation_loss_usd` reste toujours `None`.
- **PR2** (objet de la revue) : raccorder PR1 à la pipeline Data et **calibrer la volatilité sur l'historique du prix oracle Morpho**, avec une provenance vérifiable.

## 2. Ce qui a été fait

Chaîne : stores Data (SQLite : DTO + preuves ; Parquet : série `ORACLE_PRICE`) → provider analytics (snapshot, chaque point relié à sa preuve RPC par (oracle, bloc), car Parquet ne stocke pas les identifiants de preuve) → adapter (mapping et contrôles) → calibration → `MonteCarloRequest` → `run_monte_carlo` → `POST /api/v1/monte-carlo`.

**Mapping :** `collateral_amount` → quantité de PT ; `borrow_amount` → dette (jamais les totaux du marché) ; `morpho.lltv` → LLTV ; `morpho.oracle_price.value` → prix oracle initial ; `strategy.expiry` → expiry du PT.

**Contrôles de l'adapter :**
- chaîne 1 ; jointure par adresse et identifiant, jamais par symbole ; pas de mélange REAL/DEMO ;
- marché, stratégie, PT et loan token identiques ; oracle `MORPHO_ORACLE` coté en loan token ;
- fraîcheur 300 s (position, marché, oracle) et 24 h (stratégie), pas de date future ;
- en REAL : preuve RPC de l'oracle au bloc du marché ; pour une position `ONCHAIN`, preuve RPC Morpho au même bloc.

Une incohérence d'identité renvoie HTTP 422. Une donnée absente renvoie HTTP 200 avec le statut `UNAVAILABLE` et des `missing_fields` précis, sans simulation ni recours à la fixture.

**Règle de portée :** `REAL_DATA` seulement si le marché est REAL, la position `ONCHAIN` et la calibration observée vérifiée. Une position `SIMULATED` reste `HYPOTHETICAL`, même sur un marché réel.

**Calibration `ORACLE_DAILY_LOG_RETURNS_30D_V1` :**
- 31 cibles = minuits UTC ; la plus récente est le dernier minuit ≤ `as_of` ;
- pour chaque cible, le dernier point valide dans `[cible − 1 h, cible]` ; pas d'interpolation ; une cible manquante rend la calibration indisponible ;
- point valide : valeur > 0, statut OK, bloc connu, preuve RPC `eth_call price()` de l'oracle du marché au même bloc, avec hash de bloc, récupérée au plus tard à `as_of`. La valeur brute de la preuve, divisée par `10^(36 + déc. loan − déc. PT)`, doit égaler exactement la valeur stockée ;
- blocs strictement croissants ; une seule source (`ethereum_rpc`) ;
- 30 rendements `ln(P_k/P_{k−1})`, écart-type d'échantillon (n−1), annualisé par √365, en Decimal (précision 60, arrondi 1e-18) ;
- drift fixé à 0 par politique, jamais estimé ; volatilité > 5 → indisponible.

**Collecte :**
- rétrospective : `eth_call price()` au dernier bloc ≤ chaque minuit. Recherche du bloc : estimation à 12 s par slot (borne basse garantie après le Merge), encadrement puis bissection. Exige un RPC archive. Bornée à 60 jours, arrêt après 3 échecs consécutifs ;
- prospective : le refresh existant écrit un point oracle prouvé à chaque exécution.

**Tests :** 45 tests PR2 sur fixtures explicitement synthétiques ; la suite complète en compte 291, tous réussis.

## 3. Exécution sur des données réelles (10 octobre 2026)

- Marché `0x0c93c623…9689`, oracle `0xf9c1d695…7570`, LLTV 0,86, stratégie `VERIFIED_ACTIVE`, expiry 7 janvier 2027.
- L'oracle a été **déployé le 30 septembre 2026** (bloc 26090282). Seuls 10 minuits existent, tous collectés et prouvés. La calibration renvoie donc `UNAVAILABLE` (21 échantillons manquants, tous antérieurs au déploiement). Elle deviendra possible le 31 octobre.
- Les 10 prix réels augmentent **exactement** de 0,000410958904109589 par jour (= 0,15/365). C'est un **oracle PT à décote linéaire**, déterministe : volatilité mesurée ≈ 0,00001/an.
- Simulation sur les vraies données de marché avec une **position hypothétique** (2 M PT, 1,2 M USDC de dette), volatilité supposée, 20 000 trajectoires, seed 42, dette gelée :

| Vol. supposée | Horizon | Fréquence de franchissement | IC95 Wilson | LTV p95 | VaR95 (USDC) |
| --- | --- | --- | --- | --- | --- |
| 10 % | 7 j | 0 | [0 ; 0,00019] | 0,6371 | 43 897 |
| 50 % | 7 j | 0 | [0 ; 0,00019] | 0,6999 | 213 003 |
| 100 % | 1 j | 0 | [0 ; 0,00019] | 0,6794 | 161 331 |
| 100 % | 7 j | 0,0137 | [0,01218 ; 0,01541] | 0,7906 | 409 762 |

LTV initiale 0,6226, health factor 1,3814, chute oracle critique 27,61 %.

- Bug trouvé grâce aux vraies données, puis corrigé : les DTO réels n'ont pas les symboles des tokens. L'adapter exigeait le symbole ; il est désormais facultatif (l'adresse fait l'identité, et `REAL_DATA` exige l'adresse).

## 4. Limites et problèmes connus

1. **Bug préexistant hors PR2, non corrigé :** la pipeline enregistre 43 stratégies `CANDIDATE` sans leur marché Morpho, et le provider les charge toutes. La validation du snapshot échoue alors (« strategy collateral must match the Morpho market ») : toute l'API analytics plante sur un store réel. Le test réel a donc appelé l'adapter directement.
2. Aucune position `ONCHAIN` n'est collectée : `REAL_DATA` n'a jamais été exercé sur des données réelles.
3. Avec un oracle linéaire, la calibration historique donnera σ ≈ 0. Un avertissement (`LOW_OBSERVED_ORACLE_VOLATILITY_MAY_REFLECT_DETERMINISTIC_ORACLE`) le signale. Le risque réel de cette stratégie viendrait plutôt de la croissance de la dette ou de l'écart entre le prix de marché du PT et l'oracle.
4. Rendements annualisés avec un intervalle nominal d'un jour, alors que l'écart réel est d'un jour ± 1 h.
5. La provenance renvoie toutes les preuves du snapshot dans chaque réponse, ce qui grossit les réponses.

## 5. Questions précises pour la revue

1. La méthode de calibration est-elle statistiquement correcte : écart-type d'échantillon, √365, drift nul, 31 points, tolérance d'une heure ? Faut-il normaliser chaque rendement par son Δt réel ?
2. Pour un oracle PT à décote linéaire, calibrer un GBM sur l'oracle a-t-il un sens ? Que recommandes-tu : désactiver la calibration pour ce type d'oracle, modéliser le PT de marché, modéliser la dette ?
3. Vois-tu une fuite de données futures ou un mélange de sources possible dans `calibration.py` ou dans le provider ?
4. La règle `REAL_DATA` / `HYPOTHETICAL` est-elle suffisante et non trompeuse ?
5. La recherche de bloc (`block_at_or_before`) est-elle correcte, y compris en bordure (cible = timestamp exact, slots manqués, tête de chaîne) ?
6. Les résultats du tableau du §3 sont-ils plausibles ? (Vérification : σ√(7/365) ≈ 0,1385 pour σ = 1 ; ln(1 − 0,2761) ≈ −0,323.)
7. Y a-t-il une affirmation dans ce document ou dans les avertissements du code qui surestime ce qui est prouvé ?

## 6. Code clé

Le code ci-dessous est copié tel quel depuis le dépôt.

### Calibration

`backend/app/stress/monte_carlo/calibration.py`

```python
"""PR2 volatility calibration from an observed Morpho ORACLE_PRICE series.

Method ORACLE_DAILY_LOG_RETURNS_30D_V1:
  * grid: 31 UTC-midnight target times ending at the last midnight <= as_of;
  * sample: for each target, the latest valid point observed in
    [target - 1 h, target]; nothing outside this window is used, nothing is
    interpolated and a missing target makes the calibration UNAVAILABLE;
  * a point is valid only with a positive value, a block number and an RPC
    `price()` read of this market's oracle at that block whose raw value scales
    exactly to the stored value;
  * 30 log returns ln(P_k / P_k-1), sample standard deviation (n - 1),
    annualized by sqrt(365); drift is fixed at zero, not estimated;
  * points after as_of, or proven by evidence fetched after as_of, are ignored.

The result describes past oracle variability. It is not a forecast and does
not turn simulated breach frequencies into objective liquidation probabilities.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext

from pydantic import ValidationError

from ...models.evidence import DataMode, DataStatus, EvidenceDTO, SourceKind, scale_raw
from ...models.market import MORPHO_ORACLE_PRICE_SCALE, MarketDTO, PricingMethod
from ...models.timeseries import SeriesUnit, SubjectKind, TimeseriesDTO, TimeseriesMetric
from .models import (
    CALIBRATION_ANNUALIZATION_DAYS,
    CALIBRATION_RETURNS,
    CALIBRATION_SAMPLING_SECONDS,
    CALIBRATION_TOLERANCE_SECONDS,
    CalibrationPoint,
    OracleCalibration,
)

RPC_SOURCE_ID = "ethereum_rpc"
ORACLE_PRICE_METHOD = "eth_call price()"
_SAMPLING = timedelta(seconds=CALIBRATION_SAMPLING_SECONDS)
_TOLERANCE = timedelta(seconds=CALIBRATION_TOLERANCE_SECONDS)
_PRECISION = 60
_OUTPUT_QUANTUM = Decimal("1e-18")


class MonteCarloDataUnavailable(ValueError):
    """Inputs are absent or unproven: the caller reports UNAVAILABLE, never a fallback."""

    def __init__(self, missing_fields: list[str]):
        self.missing_fields = sorted(set(missing_fields))
        super().__init__("Monte Carlo inputs unavailable: " + ", ".join(self.missing_fields))


def _day_floor(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def is_sampling_candidate(timestamp: datetime) -> bool:
    """True when `timestamp` falls inside the window preceding a UTC midnight."""
    day = _day_floor(timestamp)
    return timestamp == day or day + _SAMPLING - _TOLERANCE <= timestamp


def sampling_targets(as_of: datetime) -> list[datetime]:
    """The 31 target times, oldest first; the newest is the last midnight <= as_of."""
    end = _day_floor(as_of)
    return [end - _SAMPLING * k for k in range(CALIBRATION_RETURNS, -1, -1)]


def select_oracle_series(series, market: MarketDTO) -> tuple[TimeseriesDTO | None, list[str]]:
    """Pick this market's RPC oracle series; never a spot or other-source series."""
    same_market = [s for s in series if s.subject_kind == SubjectKind.MARKET
                   and s.subject_id == market.market_id and s.chain_id == market.chain_id]
    oracle = [s for s in same_market if s.metric == TimeseriesMetric.ORACLE_PRICE]
    rpc = [s for s in oracle if s.source_id == RPC_SOURCE_ID]
    if len(rpc) == 1:
        return rpc[0], []
    if len(rpc) > 1:
        return None, ["oracle_series.ambiguous"]
    return None, ["oracle_series.source" if oracle else "oracle_series"]


def _scale_exponent(market: MarketDTO) -> int | None:
    m = market.morpho
    if m.oracle_price is not None:
        return m.oracle_price.scale_exponent
    if m.loan_token.decimals is None or m.collateral_token.decimals is None:
        return None
    return MORPHO_ORACLE_PRICE_SCALE + m.loan_token.decimals - m.collateral_token.decimals


def _proof(point, records: dict[str, EvidenceDTO], market: MarketDTO, exponent: int,
           as_of: datetime) -> str | None:
    """Evidence id of an RPC price() read matching this point exactly, else None."""
    for evidence_id in point.evidence_ids:
        e = records.get(evidence_id)
        if (e is None or e.source_kind != SourceKind.RPC or e.status != DataStatus.OK
                or e.method != ORACLE_PRICE_METHOD or e.target_address != market.morpho.oracle_address
                or e.chain_id != market.chain_id or e.data_mode != market.data_mode
                or e.block_number != point.block_number or not e.block_hash
                or e.fetched_at > as_of or not e.decoded_excerpt):
            continue
        try:
            raw = int(e.decoded_excerpt.get("out0", ""))
        except ValueError:
            continue
        if raw > 0 and scale_raw(raw, exponent) == point.value:
            return evidence_id
    return None


def calibrate_oracle_volatility(
    series: TimeseriesDTO | None,
    market: MarketDTO,
    *,
    evidence,
    as_of: datetime,
) -> OracleCalibration:
    """Return a verified calibration or raise MonteCarloDataUnavailable."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if series is None:
        raise MonteCarloDataUnavailable(["oracle_series"])
    m = market.morpho
    if m is None:
        raise MonteCarloDataUnavailable(["market.morpho"])
    missing = []
    # Identity: only a Morpho oracle series for this market, in this loan token.
    if series.metric != TimeseriesMetric.ORACLE_PRICE or series.pricing_method != PricingMethod.MORPHO_ORACLE:
        missing.append("oracle_series.metric")
    if (series.subject_kind != SubjectKind.MARKET or series.subject_id != market.market_id
            or series.chain_id != market.chain_id):
        missing.append("oracle_series.subject")
    if series.unit != SeriesUnit.TOKEN or series.quote_asset is None or not series.quote_asset.same_asset(m.loan_token):
        missing.append("oracle_series.quote_asset")
    if series.source_id != RPC_SOURCE_ID:
        missing.append("oracle_series.source")
    if series.data_mode != DataMode.REAL or market.data_mode != DataMode.REAL:
        missing.append("oracle_series.data_mode")
    exponent = _scale_exponent(market)
    if exponent is None:
        missing.append("oracle_series.scale_exponent")
    if missing:
        raise MonteCarloDataUnavailable(missing)

    records = {e.evidence_id: e for e in evidence}
    future = sum(1 for p in series.points if p.timestamp > as_of)
    usable = [p for p in series.points if p.timestamp <= as_of]
    skipped = 0
    samples: list[CalibrationPoint] = []
    for target in sampling_targets(as_of):
        candidates = [p for p in usable if target - _TOLERANCE <= p.timestamp <= target]
        chosen = None
        for point in reversed(candidates):  # latest first
            valid = (point.value is not None and point.value > 0 and point.status == DataStatus.OK
                     and point.block_number is not None)
            evidence_id = _proof(point, records, market, exponent, as_of) if valid else None
            if evidence_id is not None:
                chosen = CalibrationPoint(target_time=target, observed_at=point.timestamp,
                                          block_number=point.block_number,
                                          oracle_price_loan_per_pt=point.value, evidence_id=evidence_id)
                break
            skipped += 1
        if chosen is None:
            missing.append("oracle_series.sample:" + target.isoformat().replace("+00:00", "Z"))
        else:
            samples.append(chosen)
    if missing:
        raise MonteCarloDataUnavailable(missing)
    blocks = [p.block_number for p in samples]
    if any(b <= a for a, b in zip(blocks, blocks[1:])):
        raise MonteCarloDataUnavailable(["oracle_series.block_order"])

    with localcontext() as ctx:
        ctx.prec = _PRECISION
        try:
            returns = [(b.oracle_price_loan_per_pt / a.oracle_price_loan_per_pt).ln()
                       for a, b in zip(samples, samples[1:])]
            n = Decimal(len(returns))
            mean = sum(returns, Decimal(0)) / n
            variance = sum(((r - mean) ** 2 for r in returns), Decimal(0)) / (n - 1)
            stdev = variance.sqrt()
            annual = stdev * Decimal(CALIBRATION_ANNUALIZATION_DAYS).sqrt()
        except (InvalidOperation, ArithmeticError):
            raise MonteCarloDataUnavailable(["oracle_series.returns"]) from None
    try:
        return OracleCalibration(
            series_id=series.series_id, market_id=market.market_id, oracle_address=m.oracle_address,
            collateral_token_address=m.collateral_token.address, loan_token_address=m.loan_token.address,
            window_start=samples[0].target_time, window_end=samples[-1].target_time,
            daily_log_return_mean=mean.quantize(_OUTPUT_QUANTUM),
            daily_log_return_stdev=stdev.quantize(_OUTPUT_QUANTUM),
            annual_volatility=annual.quantize(_OUTPUT_QUANTUM),
            skipped_candidate_points=skipped, ignored_future_points=future,
            points=samples, evidence_ids=sorted({p.evidence_id for p in samples}),
        )
    except ValidationError:
        # e.g. annualized volatility above the simulation bound of 5 (500 %).
        raise MonteCarloDataUnavailable(["oracle_series.volatility_out_of_range"]) from None
```

### Adapter Data → Monte Carlo

`backend/app/stress/monte_carlo/adapter.py`

```python
"""Join Data DTOs to a Monte Carlo request without inventing any input.

Amounts, prices, LLTV and expiry come from Data. The client only chooses the
simulation settings and the volatility source:
  * USER_ASSUMPTION: client-supplied volatility/drift (PR1 semantics);
  * OBSERVED_ORACLE_SERIES: volatility calibrated by `calibration.py` on this
    market's RPC-proven oracle series; drift fixed at zero.
REAL_DATA is reserved for a verified on-chain position with an observed
calibration. A SIMULATED position, DEMO data or assumed volatility stays
HYPOTHETICAL. Missing or unproven data raises MonteCarloDataUnavailable; the
synthetic fixture is never substituted.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...models.evidence import DataMode, DataStatus, SourceKind
from ...models.market import MarketDTO, PricingMethod, Protocol, QuoteUnit
from ...models.position import PositionDTO, PositionKind
from ...models.strategy import StrategyDTO, StrategyStatus
from .calibration import MonteCarloDataUnavailable, calibrate_oracle_volatility, select_oracle_series
from .models import (
    DebtModel,
    InputScope,
    McStatus,
    MonteCarloProvenance,
    MonteCarloRequest,
    MonteCarloResult,
    MODEL_VERSION,
    PreciseDecimal,
    SimulationParams,
    SourceMode,
)

__all__ = ["MonteCarloDataRequest", "MonteCarloDataUnavailable", "build_monte_carlo_request",
           "unavailable_result"]

# Below this, the oracle barely moved: often a deterministic (e.g. linear
# discount) PT oracle, so low simulated breach counts say little about markets.
_LOW_OBSERVED_VOLATILITY = Decimal("0.01")


class MonteCarloDataRequest(BaseModel):
    """Client settings only; position amounts and prices come from Data."""
    model_config = ConfigDict(extra="forbid")
    strategy_id: str
    position_id: str
    horizon_days: Literal[1, 3, 7]
    num_paths: int = Field(default=10000, strict=True, ge=1000, le=50000)
    seed: int = Field(default=42, strict=True, ge=0)
    volatility_source: SourceMode = SourceMode.USER_ASSUMPTION
    annual_volatility: PreciseDecimal | None = Field(default=None, ge=0, le=5)
    annual_drift: PreciseDecimal | None = None
    debt_model: DebtModel = DebtModel.FROZEN_DEBT
    borrow_apr: PreciseDecimal | None = None

    @model_validator(mode="after")
    def volatility_matches_source(self):
        if self.volatility_source == SourceMode.USER_ASSUMPTION:
            if self.annual_volatility is None:
                raise ValueError("USER_ASSUMPTION requires annual_volatility")
        elif self.annual_volatility is not None or self.annual_drift not in (None, 0):
            raise ValueError("OBSERVED_ORACLE_SERIES calibrates volatility; drift is fixed at zero")
        if self.debt_model == DebtModel.CONSTANT_APR and self.borrow_apr is None:
            raise ValueError("borrow_apr is required for CONSTANT_APR")
        return self


def unavailable_result(settings: MonteCarloDataRequest, as_of: datetime,
                       missing_fields: list[str]) -> MonteCarloResult:
    """Explicit UNAVAILABLE result: no data, no fabricated metric."""
    return MonteCarloResult(
        status=McStatus.UNAVAILABLE, as_of=as_of, data=None,
        provenance=MonteCarloProvenance(calibration=settings.volatility_source, evidence_ids=[]),
        missing_fields=sorted(set(missing_fields)),
        warnings=["NO_SIMULATION_RUN", "NO_FIXTURE_SUBSTITUTED"],
    )


def build_monte_carlo_request(
    position: PositionDTO,
    market: MarketDTO,
    strategy: StrategyDTO,
    *,
    settings: MonteCarloDataRequest,
    as_of: datetime,
    evidence=(),
    series=(),
    replay: bool = False,
) -> MonteCarloRequest:
    """Map validated, fresh DTOs (and, if requested, a verified calibration)."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('as_of must be timezone-aware')
    if settings.strategy_id != strategy.strategy_id or settings.position_id != position.position_id:
        raise ValueError('Requested strategy/position does not match Data')
    if market.protocol != Protocol.MORPHO_BLUE or market.morpho is None:
        raise ValueError('A Morpho market is required')
    m = market.morpho
    if not (position.chain_id == market.chain_id == strategy.chain_id == 1):
        raise ValueError('All inputs must be Ethereum mainnet chain_id=1')
    if not (position.data_mode == market.data_mode == strategy.data_mode):
        raise ValueError('Cannot mix REAL and DEMO inputs')
    if not (position.market_id == market.market_id == strategy.morpho_market_id):
        raise ValueError('Position/market/strategy market IDs differ')
    if position.strategy_id is not None and position.strategy_id != strategy.strategy_id:
        raise ValueError('Position belongs to another strategy')
    if not (position.collateral_asset.same_asset(strategy.pt)
            and position.collateral_asset.same_asset(m.collateral_token)):
        raise ValueError('Position collateral must be the strategy PT')
    if not (position.loan_asset.same_asset(strategy.loan_asset)
            and position.loan_asset.same_asset(m.loan_token)):
        raise ValueError('Loan asset mismatch')

    missing = []
    bad = {DataStatus.ERROR, DataStatus.MISSING, DataStatus.MISMATCH, DataStatus.STALE}
    if position.status in bad:
        missing.append('position.status')
    if market.status in bad:
        missing.append('market.status')
    if strategy.data_status in bad:
        missing.append('strategy.data_status')
    if position.collateral_amount is None:
        missing.append('position.collateral_amount')
    if position.borrow_amount is None:
        missing.append('position.borrow_amount')
    if strategy.expiry is None:
        missing.append('strategy.expiry')
    for name, stamp, ttl in (
        ('position.as_of', position.as_of, 300),
        ('market.as_of', market.as_of, 300),
        ('strategy.as_of', strategy.as_of, 86400),
    ):
        if stamp > as_of or as_of - stamp > timedelta(seconds=ttl):
            missing.append(name)

    op = m.oracle_price
    if op is None:
        missing.append('market.morpho.oracle_price')
    else:
        if (op.pricing_method != PricingMethod.MORPHO_ORACLE or op.quote_unit != QuoteUnit.TOKEN
                or not op.base_asset.same_asset(strategy.pt)
                or op.quote_asset is None or not op.quote_asset.same_asset(position.loan_asset)
                or op.oracle_address != m.oracle_address):
            raise ValueError('Oracle price must quote this PT in this loan token')
        if op.value <= 0 or op.status in bad:
            missing.append('market.morpho.oracle_price.value')
        if op.observed_at > as_of or as_of - op.observed_at > timedelta(seconds=300):
            missing.append('market.morpho.oracle_price.observed_at')
        # DEMO markets carry no on-chain block; the same-block proof applies to REAL reads.
        if market.data_mode == DataMode.REAL and market.block_number != op.block_number:
            missing.append('market.oracle_block_consistency')

    records = {e.evidence_id: e for e in evidence}
    ids = set(position.evidence_ids) | set(market.evidence_ids) | set(strategy.evidence_ids)
    if op is not None:
        ids.update(op.evidence_ids)
    for eid in ids:
        ev = records.get(eid)
        if (ev is None or ev.chain_id != market.chain_id or ev.data_mode != market.data_mode
                or ev.status in bad or ev.fetched_at > as_of):
            missing.append('evidence:' + eid)

    if market.data_mode == DataMode.REAL:
        if market.status not in (DataStatus.OK, DataStatus.PARTIAL):
            missing.append('market.verification')
        if strategy.status not in (StrategyStatus.VERIFIED_ACTIVE, StrategyStatus.EXPIRED):
            missing.append('strategy.verification')
        if op is not None:
            proofs = [records[eid] for eid in op.evidence_ids if eid in records]
            if not any(e.source_kind == SourceKind.RPC and e.target_address == m.oracle_address
                       and e.block_number == op.block_number and e.block_hash
                       and as_of - e.fetched_at <= timedelta(seconds=300) for e in proofs):
                missing.append('oracle.rpc_evidence')
        if position.kind == PositionKind.ONCHAIN:
            if position.status not in (DataStatus.OK, DataStatus.PARTIAL):
                missing.append('position.verification')
            if position.block_number != market.block_number:
                missing.append('position.market_block_consistency')
            proofs = [records[eid] for eid in position.evidence_ids if eid in records]
            if not any(e.source_kind == SourceKind.RPC and e.block_number == position.block_number
                       and e.target_address == m.morpho_address and e.block_hash for e in proofs):
                missing.append('position.rpc_evidence')

    calibration = None
    if settings.volatility_source == SourceMode.OBSERVED_ORACLE_SERIES:
        chosen, series_missing = select_oracle_series(series, market)
        try:
            if series_missing:
                raise MonteCarloDataUnavailable(series_missing)
            calibration = calibrate_oracle_volatility(chosen, market, evidence=evidence, as_of=as_of)
        except MonteCarloDataUnavailable as exc:
            missing.extend(exc.missing_fields)
    if missing:
        raise MonteCarloDataUnavailable(missing)

    warnings = ['INPUT_DATA_MODE_' + market.data_mode.value]
    if calibration is None:
        warnings.append('VOLATILITY_AND_DRIFT_ARE_USER_ASSUMPTIONS_NOT_CALIBRATED')
        simulation = SimulationParams(
            model=MODEL_VERSION, source_mode=SourceMode.USER_ASSUMPTION,
            annual_volatility=settings.annual_volatility,
            annual_drift=settings.annual_drift if settings.annual_drift is not None else 0,
            debt_model=settings.debt_model, borrow_apr=settings.borrow_apr,
        )
    else:
        warnings.append('VOLATILITY_CALIBRATED_ON_OBSERVED_ORACLE_SERIES')
        if calibration.annual_volatility < _LOW_OBSERVED_VOLATILITY:
            warnings.append('LOW_OBSERVED_ORACLE_VOLATILITY_MAY_REFLECT_DETERMINISTIC_ORACLE')
        simulation = SimulationParams(
            model=MODEL_VERSION, source_mode=SourceMode.OBSERVED_ORACLE_SERIES,
            annual_volatility=calibration.annual_volatility, annual_drift=calibration.annual_drift,
            debt_model=settings.debt_model, borrow_apr=settings.borrow_apr, calibration=calibration,
        )
        ids.update(calibration.evidence_ids)
    if position.kind == PositionKind.SIMULATED:
        warnings.append('HYPOTHETICAL_POSITION_AMOUNTS')
    if replay:
        warnings.append('FROZEN_REPLAY_NOT_LIVE')
    real_data = (market.data_mode == DataMode.REAL and position.kind == PositionKind.ONCHAIN
                 and calibration is not None)
    return MonteCarloRequest(
        strategy_id=strategy.strategy_id, position_id=position.position_id,
        snapshot_as_of=as_of,
        input_scope=InputScope.REAL_DATA if real_data else InputScope.HYPOTHETICAL,
        loan_token_symbol=position.loan_asset.symbol, loan_token_address=position.loan_asset.address,
        collateral_quantity_pt=position.collateral_amount,
        initial_oracle_price_loan_per_pt=op.value, debt_loan_units=position.borrow_amount,
        lltv=m.lltv, horizon_days=settings.horizon_days, num_paths=settings.num_paths,
        seed=settings.seed, pt_expiry=strategy.expiry, simulation=simulation,
        evidence_ids=sorted(ids), warnings=warnings,
    )
```

### Modèles : calibration et validations de la requête

`backend/app/stress/monte_carlo/models.py`

```python
CALIBRATION_METHOD = "ORACLE_DAILY_LOG_RETURNS_30D_V1"
CALIBRATION_RETURNS = 30
CALIBRATION_SAMPLING_SECONDS = 86400
CALIBRATION_TOLERANCE_SECONDS = 3600
CALIBRATION_ANNUALIZATION_DAYS = 365


class CalibrationPoint(BaseModel):
    """One sampled Morpho oracle observation and the RPC read proving it."""
    model_config = ConfigDict(extra="forbid", validate_default=True)
    target_time: datetime
    observed_at: datetime
    block_number: int = Field(strict=True, ge=0)
    oracle_price_loan_per_pt: PreciseDecimal = Field(gt=0)
    evidence_id: str

    @field_validator("target_time", "observed_at")
    @classmethod
    def aware_times(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def observed_within_tolerance(self) -> CalibrationPoint:
        lag = (self.target_time - self.observed_at).total_seconds()
        if not 0 <= lag <= CALIBRATION_TOLERANCE_SECONDS:
            raise ValueError("Sample must be observed at most one tolerance before its target time")
        return self


class OracleCalibration(BaseModel):
    """Auditable PR2 calibration from one Morpho oracle series.

    31 daily samples give 30 log returns; volatility is annualized over 365 days.
    Drift is a fixed policy (zero), never estimated from 30 noisy returns.
    """
    model_config = ConfigDict(extra="forbid", validate_default=True)
    method: Literal["ORACLE_DAILY_LOG_RETURNS_30D_V1"] = CALIBRATION_METHOD
    series_id: str
    source_id: Literal["ethereum_rpc"] = "ethereum_rpc"
    chain_id: Literal[1] = 1
    market_id: str
    oracle_address: str
    collateral_token_address: str
    loan_token_address: str
    sampling_interval_seconds: Literal[86400] = CALIBRATION_SAMPLING_SECONDS
    sample_tolerance_seconds: Literal[3600] = CALIBRATION_TOLERANCE_SECONDS
    annualization_days: Literal[365] = CALIBRATION_ANNUALIZATION_DAYS
    window_start: datetime
    window_end: datetime
    observation_count: Literal[31] = CALIBRATION_RETURNS + 1
    return_count: Literal[30] = CALIBRATION_RETURNS
    daily_log_return_mean: PreciseDecimal
    daily_log_return_stdev: PreciseDecimal = Field(ge=0)
    annual_volatility: PreciseDecimal = Field(ge=0, le=5)
    annual_drift: PreciseDecimal = Decimal("0")
    drift_policy: Literal["FIXED_ZERO_NOT_ESTIMATED"] = "FIXED_ZERO_NOT_ESTIMATED"
    skipped_candidate_points: int = Field(default=0, strict=True, ge=0)
    ignored_future_points: int = Field(default=0, strict=True, ge=0)
    points: list[CalibrationPoint] = Field(min_length=31, max_length=31)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("window_start", "window_end")
    @classmethod
    def aware_window(cls, value):
        return _require_timezone(value)

    @model_validator(mode="after")
    def ordered_window(self):
        if self.annual_drift != 0:
            raise ValueError("Calibration drift policy is fixed at zero")
        span = (self.window_end - self.window_start).total_seconds()
        if span != CALIBRATION_RETURNS * CALIBRATION_SAMPLING_SECONDS:
            raise ValueError("Calibration window must span exactly 30 sampling intervals")
        targets = [p.target_time for p in self.points]
        if targets[0] != self.window_start or targets[-1] != self.window_end or any(
            (b - a).total_seconds() != CALIBRATION_SAMPLING_SECONDS for a, b in zip(targets, targets[1:])
        ):
            raise ValueError("Calibration points must sit on the daily sampling grid")
        blocks = [p.block_number for p in self.points]
        if any(b <= a for a, b in zip(blocks, blocks[1:])):
            raise ValueError("Calibration blocks must be strictly increasing")
        if sorted(self.evidence_ids) != sorted({p.evidence_id for p in self.points}):
            raise ValueError("Calibration evidence_ids must be exactly the sampled points' proofs")
        return self


class SimulationParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    model: Literal["GBM_ORACLE_PRICE_V1"]
    source_mode: SourceMode
    annual_volatility: PreciseDecimal = Field(ge=0, le=5)
    annual_drift: PreciseDecimal = Decimal("0")
    debt_model: DebtModel
    borrow_apr: PreciseDecimal | None = None

    calibration: OracleCalibration | None = None

    @model_validator(mode="after")
    def validate_debt_model(self) -> SimulationParams:
        if self.source_mode == SourceMode.OBSERVED_ORACLE_SERIES:
            if self.calibration is None:
                raise ValueError("PR2 requires verified oracle calibration for OBSERVED_ORACLE_SERIES")
            if (self.annual_volatility != self.calibration.annual_volatility
                    or self.annual_drift != self.calibration.annual_drift):
                raise ValueError("Simulation parameters must match oracle calibration")
        elif self.calibration is not None:
            raise ValueError("USER_ASSUMPTION cannot carry observed calibration")
        if self.debt_model == DebtModel.CONSTANT_APR and self.borrow_apr is None:
            raise ValueError("borrow_apr is required for CONSTANT_APR")
        return self


class MonteCarloRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    strategy_id: str
    position_id: str
    position_side: PositionSide = PositionSide.BORROW
    snapshot_as_of: datetime
    input_scope: InputScope
    # Display label only; the address is the identity. Data may not know the symbol.
    loan_token_symbol: str | None = None
    loan_token_address: str | None = None
    collateral_quantity_pt: PreciseDecimal = Field(gt=0)
    initial_oracle_price_loan_per_pt: PreciseDecimal = Field(gt=0)
    debt_loan_units: PreciseDecimal = Field(ge=0)
    lltv: PreciseDecimal = Field(gt=0, le=1)
    horizon_days: Literal[1, 3, 7]
    num_paths: int = Field(strict=True, ge=1000, le=50000)
    seed: int = Field(strict=True)
    pt_expiry: datetime | None = None
    simulation: SimulationParams
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("horizon_days", mode="before")
    @classmethod
    def validate_horizon_type(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("horizon_days must be an integer: 1, 3 or 7")
        return value

    @field_validator("snapshot_as_of")
    @classmethod
    def validate_snapshot_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @field_validator("pt_expiry")
    @classmethod
    def validate_expiry_timezone(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_timezone(value)

    @model_validator(mode="after")
    def validate_real_data_requirements(self) -> MonteCarloRequest:
        if not self.loan_token_symbol and not self.loan_token_address:
            raise ValueError("loan token requires a symbol or an address")
        if self.simulation.calibration is not None:
            c = self.simulation.calibration
            if c.window_end > self.snapshot_as_of:
                raise ValueError("Calibration cannot use future observations")
            if not set(c.evidence_ids).issubset(self.evidence_ids):
                raise ValueError("Request must include calibration evidence_ids")
        if self.input_scope == InputScope.REAL_DATA:
            if not self.evidence_ids:
                raise ValueError("REAL_DATA requires non-empty evidence_ids")
            if self.simulation.source_mode != SourceMode.OBSERVED_ORACLE_SERIES:
                raise ValueError(
                    "REAL_DATA requires OBSERVED_ORACLE_SERIES; "
                    "PR2 requires observed oracle calibration"
                )
            if not self.loan_token_address:
                raise ValueError("REAL_DATA requires loan_token_address")
        return self
```

### Provider : série oracle et preuves

`backend/app/analytics/provider.py`

```python
def _oracle_series(store, parquet, market, data_mode):
    """RPC oracle history of one Morpho market, reduced to calibration sampling candidates.

    Parquet rows carry no evidence ids; each point is linked to the RPC reads of the
    market oracle at its block. The calibration re-checks every proof.
    """
    try:
        rows = parquet.read_observations(market.market_key, TimeseriesMetric.ORACLE_PRICE.value,
                                         data_mode, source_id=RPC_SOURCE_ID)
    except ObservationError:
        return None, ()  # unusable history stays absent; Monte Carlo reports it missing
    rows = [r for r in rows if is_sampling_candidate(r.ts_utc)]
    if rows:
        rows = [r for r in rows if r.ts_utc >= rows[-1].ts_utc - timedelta(days=_ORACLE_HISTORY_DAYS)]
    if not rows:
        return None, ()
    oracle = market.morpho.oracle_address
    proofs = {}
    for e in store.find_rpc_evidence(oracle, {r.block_number for r in rows if r.block_number is not None}, data_mode):
        if e.method == ORACLE_PRICE_METHOD:
            proofs.setdefault(e.block_number, []).append(e)
    points = [TimeseriesPointDTO(
        timestamp=r.ts_utc, value=r.value_decimal,
        status=next((DataStatus(f) for f in r.quality_flags if f in _FLAG_STATUSES), DataStatus.OK),
        block_number=r.block_number,
        evidence_ids=tuple(e.evidence_id for e in proofs.get(r.block_number, ())),
    ) for r in rows]
    statuses = {p.status for p in points}
    try:
        dto = TimeseriesDTO(
            chain_id=market.chain_id, data_mode=data_mode,
            series_id="ts_oracle_price_" + hashlib.sha256(market.market_key.encode()).hexdigest()[:24],
            metric=TimeseriesMetric.ORACLE_PRICE, subject_kind=SubjectKind.MARKET, subject_id=market.market_id,
            unit=SeriesUnit(rows[0].unit), quote_asset=market.morpho.loan_token,
            pricing_method=PricingMethod.MORPHO_ORACLE, source_id=RPC_SOURCE_ID,
            status=DataStatus.OK if statuses == {DataStatus.OK} else
            DataStatus.PARTIAL if any(p.value is not None for p in points) else DataStatus.MISSING,
            points=tuple(points),
        )
    except ValueError:
        return None, ()
    return dto, tuple(e for es in proofs.values() for e in es)
```

### Route API

`backend/app/api/routes_analytics.py`

```python
    @router.post("/monte-carlo", response_model=Envelope[MonteCarloResult],
                 summary="Oracle-price Monte Carlo on Data inputs; volatility assumed or calibrated on the oracle series")
    def monte_carlo(request: MonteCarloDataRequest, service=Depends(resolve)):
        snapshot = service.snapshot
        strategy = snapshot.strategy(request.strategy_id)
        position = snapshot.position(request.strategy_id, request.position_id)
        now = service.now()
        try:
            with localcontext() as ctx:
                ctx.prec = 60
                mapped = build_monte_carlo_request(
                    position, snapshot.market(position), strategy, settings=request,
                    as_of=now, evidence=snapshot.evidence, series=snapshot.series,
                    replay=service.replay,
                )
        except MonteCarloDataUnavailable as exc:
            # Business status, not a transport error: nothing is simulated or substituted.
            result = unavailable_result(request, now, exc.missing_fields)
        else:
            result = run_monte_carlo(mapped)
        response = envelope(service, result, result.status.value)
        response.missing_fields = tuple(result.missing_fields)
        return response
```

### Collecte rétrospective

`backend/app/ingestion/oracle_history.py`

```python
"""Retrospective Morpho oracle history: IOracle.price() at past blocks via eth_call.

One read per UTC midnight: the last block whose timestamp is <= that midnight,
which is the sampling grid of the Monte Carlo calibration. Every read keeps its
RPC evidence (block number and hash, raw value), and the Parquet row keeps the
block number so the analytics provider can re-link the proof.

Past-block eth_call needs historical state: most full or public nodes only keep
recent blocks, so an archive-capable ETHEREUM_RPC_URL is usually required. A
failing read is reported, never replaced; the run stops after a few
consecutive failures instead of hammering the RPC. No transaction, no signing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.adapters.ethereum_rpc import (
    EthereumRPCError,
    PinnedBlock,
    read_erc20_decimals,
    read_morpho_market_params,
    read_oracle_price,
)
from app.ingestion.normalize import morpho_market_key, oracle_price
from app.models.evidence import DataMode, normalize_address, normalize_bytes32
from app.models.market import PricingMethod, TokenRef
from app.models.timeseries import SeriesUnit, TimeseriesMetric
from app.storage.parquet_store import Observation

SLOT_SECONDS = 12
# First proof-of-stake block: from here on, consecutive blocks are >= 12 s apart.
MERGE_TIMESTAMP = datetime(2022, 9, 15, 6, 42, 59, tzinfo=timezone.utc)
MAX_DAYS = 60


@dataclass
class OracleSample:
    target_time: datetime
    block_number: int | None = None
    block_time: datetime | None = None
    value: str | None = None
    error: str | None = None


@dataclass
class OracleBackfillReport:
    market_id: str
    oracle_address: str | None = None
    samples: list[OracleSample] = field(default_factory=list)
    written_points: int = 0
    written_evidence: int = 0
    stopped_reason: str | None = None

    @property
    def ok_count(self) -> int:
        return sum(1 for s in self.samples if s.value is not None)


def block_at_or_before(rpc, target: datetime, latest: PinnedBlock) -> PinnedBlock:
    """Last block with timestamp <= target (post-merge), by bracketing then bisection."""
    if target >= latest.timestamp:
        return latest
    if target < MERGE_TIMESTAMP:
        raise ValueError("targets before the merge are not supported")
    # Blocks are >= 12 s apart, so this block is at or before the target.
    lo = rpc.pin_block(max(0, latest.number - math.ceil((latest.timestamp - target).total_seconds() / SLOT_SECONDS)))
    if lo.timestamp > target:
        raise ValueError(f"block {lo.number} after target {target.isoformat()}: slot assumption broken")
    step = 64
    while True:
        hi = rpc.pin_block(min(latest.number, lo.number + step))
        if hi.timestamp > target:
            break
        lo, step = hi, step * 2
    while hi.number - lo.number > 1:
        mid = rpc.pin_block((lo.number + hi.number) // 2)
        if mid.timestamp <= target:
            lo = mid
        else:
            hi = mid
    return lo


def daily_targets(now: datetime, days: int) -> list[datetime]:
    """The last `days` UTC midnights <= now, oldest first."""
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return [end - timedelta(days=k) for k in range(days - 1, -1, -1)]


def run_oracle_backfill(
    rpc,
    *,
    market_id: str,
    sqlite,
    parquet,
    now: datetime,
    days: int = 31,
    expected_collateral: str | None = None,
    expected_loan: str | None = None,
    dry_run: bool = False,
    max_consecutive_failures: int = 3,
) -> OracleBackfillReport:
    """Read, prove and store one oracle price per UTC midnight over `days` midnights."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")
    market_id = normalize_bytes32(market_id)
    report = OracleBackfillReport(market_id=market_id)
    latest = rpc.pin_block()
    params = read_morpho_market_params(rpc, market_id, latest)
    if expected_collateral and params.collateral_token != normalize_address(expected_collateral):
        raise ValueError("market collateral is not the expected PT")
    if expected_loan and params.loan_token != normalize_address(expected_loan):
        raise ValueError("market loan token is not the expected loan token")
    report.oracle_address = params.oracle
    collateral = TokenRef(chain_id=1, address=params.collateral_token,
                          decimals=int(read_erc20_decimals(rpc, params.collateral_token, latest).value))
    loan = TokenRef(chain_id=1, address=params.loan_token,
                    decimals=int(read_erc20_decimals(rpc, params.loan_token, latest).value))

    rows: list[Observation] = []
    failures = 0
    for target in daily_targets(now, days):
        sample = OracleSample(target_time=target)
        report.samples.append(sample)
        try:
            pinned = block_at_or_before(rpc, target, latest)
            sample.block_number, sample.block_time = pinned.number, pinned.timestamp
            read = read_oracle_price(rpc, params.oracle, pinned)
            dto, reason = oracle_price(read, collateral, loan)
            if dto is None:
                raise ValueError(f"oracle price not normalisable: {reason}")
        except (EthereumRPCError, ValueError) as exc:
            sample.error = f"{type(exc).__name__}: {exc}"[:300]
            failures += 1
            if failures >= max_consecutive_failures:
                report.stopped_reason = (f"{failures} consecutive failures; past-block reads usually "
                                         "need an archive-capable ETHEREUM_RPC_URL")
                break
            continue
        failures = 0
        sample.value = str(dto.value)
        fetched_at = rpc.evidence[read.ref.evidence_id].fetched_at
        rows.append(Observation(
            entity_id=morpho_market_key(market_id), metric=TimeseriesMetric.ORACLE_PRICE.value,
            ts_utc=pinned.timestamp, value_decimal=dto.value, unit=SeriesUnit.TOKEN.value,
            source_id=read.ref.source_id, retrieved_at=fetched_at, block_number=pinned.number,
            pricing_method=PricingMethod.MORPHO_ORACLE.value,
        ))
    if not dry_run:
        report.written_evidence = sqlite.save_evidence(rpc.evidence.values())
        if rows:
            parquet.write_observations(rows, DataMode.REAL)
        report.written_points = len(rows)
    return report
```
