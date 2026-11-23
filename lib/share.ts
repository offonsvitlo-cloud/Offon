import { getTelegram } from './api'
import { queueLabel, translate, type Lang } from './i18n'
import { dayTitle, dayTotals, formatHours, halfSlots, hourKind, offIntervals, type HourKind, type SlotValue } from './schedule'

const EMOJI: Record<HourKind, string> = {
  on: '🟩',
  off: '🟥',
  'off-first': '🟧',
  'off-second': '🟧',
  maybe: '🟨',
  unknown: '⬜',
}

/** Текст графика очереди для пересылки друзьям (без названия адреса: оно личное). */
export function buildScheduleText(opts: {
  lang: Lang
  queue: string
  dayKey: string
  hours: Record<string, SlotValue>
  update?: string | null
}): string {
  const { lang, queue, dayKey, hours, update } = opts
  const t = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) => translate(lang, key, vars)
  const pad = (n: number) => String(n).padStart(2, '0')
  const rows: string[] = []
  for (let r = 0; r < 4; r++) {
    let cells = ''
    for (let c = 0; c < 6; c++) cells += EMOJI[hourKind(hours[String(r * 6 + c + 1)])]
    rows.push(`${pad(r * 6)}–${pad(r * 6 + 5)} ${cells}`)
  }
  const slots = halfSlots(hours)
  const intervals = offIntervals(slots)
  const totals = dayTotals(slots)
  const lines = [
    `⚡ ${t('sched.title')}`,
    `${queueLabel(lang, queue)} · ${dayTitle(dayKey, lang)}`,
    '',
    ...rows,
    t('sched.legend'),
    '',
    intervals.length ? `🔴 ${t('sched.outages', { list: intervals.join(', ') })}` : `✅ ${t('sched.noOutages')}`,
    `💡 ${t('sched.totals', {
      on: formatHours(totals.onHours),
      off: formatHours(totals.offHours),
      h: t('unit.h'),
    })}`,
  ]
  if (update) lines.push(t('sched.updated', { v: update }))
  return lines.join('\n')
}

/** Окно «Поделиться» в Telegram; затем системное меню; затем обычная ссылка. */
export function shareViaDialog(text: string, link: string): boolean {
  const base = link || (typeof location !== 'undefined' ? location.origin : '')
  const url = `https://t.me/share/url?url=${encodeURIComponent(base)}&text=${encodeURIComponent(text)}`
  const tg = getTelegram()
  try {
    if (tg?.openTelegramLink) {
      tg.openTelegramLink(url)
      return true
    }
  } catch {}
  const nav = navigator as Navigator & { share?: (d: { url?: string; text?: string }) => Promise<void> }
  if (typeof nav.share === 'function') {
    nav.share({ url: base, text }).catch(() => {})
    return true
  }
  return Boolean(window.open(url, '_blank', 'noopener'))
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.cssText = 'position:fixed;opacity:0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}
