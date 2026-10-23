/** Round-number ticks from 0 up to just past the largest value; counts only, so whole numbers. */
export function niceTicks(max: number): number[] {
  const top = Math.max(1, Math.ceil(max))
  const step = top <= 4 ? 1 : top <= 10 ? 2 : top <= 20 ? 5 : Math.pow(10, Math.floor(Math.log10(top))) * (top / Math.pow(10, Math.floor(Math.log10(top))) <= 2 ? 0.5 : 1)
  const ticks: number[] = []
  for (let v = 0; v <= top + step - 1; v += step) {
    ticks.push(v)
    if (v >= top) break
  }
  return ticks
}
