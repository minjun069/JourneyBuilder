import { toCanonicalMoney } from '../utils/canonicalMoney.js'

export function mapMonthlyIncome(screenValue) {
  if (screenValue === '' || screenValue == null) return null
  return toCanonicalMoney(String(screenValue))
}

export function wrapCurrentListForm(items) {
  return { assets: items }
}
