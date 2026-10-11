import { describe, expect, it } from 'vitest'
import { decimalText, fmtRatioPct, fmtUsd, num, percentTextToRatio } from './format'

describe('percentTextToRatio', () => {
  it('converts user percents to exact Decimal ratios', () => {
    expect(percentTextToRatio('2')).toBe('0.02')
    expect(percentTextToRatio('10')).toBe('0.1')
    expect(percentTextToRatio('5,275')).toBe('0.05275')
    expect(percentTextToRatio('0.5')).toBe('0.005')
    expect(percentTextToRatio('100')).toBe('1')
    expect(percentTextToRatio('250')).toBe('2.5')
    expect(percentTextToRatio('0')).toBe('0')
    expect(percentTextToRatio('-40')).toBe('-0.4')
  })

  it('rejects text that is not a number', () => {
    expect(percentTextToRatio('')).toBeNull()
    expect(percentTextToRatio('abc')).toBeNull()
    expect(percentTextToRatio('1e3')).toBeNull()
  })
})

describe('display helpers', () => {
  it('keeps unknown values unknown instead of zero', () => {
    expect(num(null)).toBeNull()
    expect(fmtUsd(null)).toBe('Unavailable')
    expect(fmtRatioPct(undefined)).toBe('Unavailable')
    expect(fmtUsd('1234567.4')).toBe('1,234,567 USD')
    expect(fmtRatioPct('0.0414', 2)).toBe('4.14%')
  })

  it('normalises decimal input', () => {
    expect(decimalText('1 000,5')).toBe('1000.5')
    expect(decimalText('12a')).toBeNull()
  })
})
