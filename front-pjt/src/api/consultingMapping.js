import { toCanonicalMoney } from '../utils/canonicalMoney.js'

export function mapMonthlyIncome(screenValue) {
  return toCanonicalMoney(String(screenValue))
}
