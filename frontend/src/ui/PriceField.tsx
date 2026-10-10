import { useState } from 'react'
import { PRICE_SOURCE_LABEL } from '../data/usePositionInputs'
import type { PositionModel } from '../data/usePositionInputs'

/** Collateral price used by the estimates, with its source; editable. */
export default function PriceField({ model }: { model: PositionModel }) {
  const [text, setText] = useState(model.price === null ? '' : String(model.price))
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor="collateral-price" className="flex justify-between text-xs text-ink-2">
        <span>
          Collateral price ({model.loanSymbol} per {model.collateralSymbol})
        </span>
      </label>
      <input
        id="collateral-price"
        type="number"
        inputMode="decimal"
        min="0"
        step="0.0001"
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          const v = Number(e.target.value)
          model.setManualPrice(e.target.value === '' || !Number.isFinite(v) || v <= 0 ? null : v)
        }}
        className="h-8 rounded-md border border-[#243049] bg-control px-2.5 font-mono text-ink"
      />
      <span className="text-[10.5px] text-ink-4">
        {model.priceSource ? PRICE_SOURCE_LABEL[model.priceSource] : 'No price available'}
      </span>
    </div>
  )
}
