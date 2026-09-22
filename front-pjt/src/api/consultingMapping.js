import { toCanonicalMoney } from '../utils/canonicalMoney.js'

// 화면도 원 단위다. String() 으로 감싸면 number 분기(이미 KRW)를 우회해
// AI·서버가 채운 값이 다시 만 단위로 해석되므로 감싸지 않는다.
export function mapMonthlyIncome(screenValue) {
  if (screenValue === '' || screenValue == null) return null
  return toCanonicalMoney(screenValue)
}

export function wrapCurrentListForm(items) {
  return { assets: items }
}
