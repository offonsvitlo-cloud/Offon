import { analyzeNow, dayTotals, findDayKeys, halfSlots, kyivClock, type Snapshot } from './schedule'

export function queueStatus(snapshot: Snapshot, queue: string, now: Date) {
  const { todayKey, tomorrowKey } = findDayKeys(snapshot, now)
  const today = halfSlots(snapshot.days?.[todayKey]?.[queue])
  const tomorrow = halfSlots(tomorrowKey ? snapshot.days?.[tomorrowKey]?.[queue] : undefined)
  const clock = kyivClock(now)
  return {
    todayKey,
    tomorrowKey,
    today,
    tomorrow,
    clock,
    now: analyzeNow(today, tomorrow, clock.minutesOfDay),
    totals: dayTotals(today),
  }
}

export type QueueStatus = ReturnType<typeof queueStatus>
