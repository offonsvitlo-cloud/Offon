import { getTelegram } from './api'
import { queueLabel, translate, type Lang } from './i18n'
import { dayTitle, dayTotals, formatDuration, formatHours, halfSlots, slotLabel, type SlotValue } from './schedule'

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
  const slots = halfSlots(hours)

  // подряд идущие получасы с одинаковым статусом объединяем в один промежуток
  const lines: string[] = []
  let start = 0
  for (let i = 1; i <= slots.length; i++) {
    if (i < slots.length && slots[i] === slots[start]) continue
    const state = slots[start]
    const from = slotLabel(start)
    const to = i === slots.length ? '24:00' : slotLabel(i)
    const mins = (i - start) * 30
    const dur = formatDuration(mins, lang)
    if (state === 'off') lines.push(`🔴 ${from}–${to} · ${t('sched.off')} (${dur})`)
    else if (state === 'maybe') lines.push(`🟡 ${from}–${to} · ${t('sched.maybe')} (${dur})`)
    else if (state === 'on') lines.push(`🟢 ${from}–${to} · ${t('sched.on')}`)
    else lines.push(`⚪ ${from}–${to} · ${t('sched.unknown')}`)
    start = i
  }

  const totals = dayTotals(slots)
  const hasOff = slots.some((s) => s === 'off')
  const out = [
    `⚡ ${t('sched.title')}`,
    `${queueLabel(lang, queue)} · ${dayTitle(dayKey, lang)}`,
    '',
    ...(hasOff ? [] : [`✅ ${t('sched.noOutages')}`]),
    ...lines,
    '',
    `💡 ${t('sched.totals', { on: formatHours(totals.onHours), off: formatHours(totals.offHours), h: t('unit.h') })}`,
  ]
  if (update) out.push(t('sched.updated', { v: update }))
  return out.join('\n')
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
