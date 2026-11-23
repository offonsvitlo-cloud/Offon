'use client'

import { useEffect, useMemo, useState } from 'react'
import { Copy, ImagePlus, LoaderCircle, Send, X } from 'lucide-react'
import { api, haptic, json } from '@/lib/api'
import { debugLog } from '@/lib/ads'
import { buildScheduleText, copyText, shareViaDialog } from '@/lib/share'
import type { Snapshot } from '@/lib/schedule'
import { cn } from '@/lib/utils'
import { useT } from '@/hooks/use-app-data'
import { useShareMessage } from '@/hooks/use-share'

interface Props {
  onClose: () => void
  snapshot: Snapshot
  queue: string
  todayKey: string | null
  tomorrowKey: string | null
  startTomorrow: boolean
  link: string
}

export function ShareScheduleSheet({ onClose, snapshot, queue, todayKey, tomorrowKey, startTomorrow, link }: Props) {
  const { t, lang } = useT()
  const tomorrowHours = tomorrowKey ? snapshot.days?.[tomorrowKey]?.[queue] : undefined
  const todayHours = todayKey ? snapshot.days?.[todayKey]?.[queue] : undefined
  const hasTomorrow = Boolean(tomorrowHours && Object.keys(tomorrowHours).length)
  const hasToday = Boolean(todayHours && Object.keys(todayHours).length)
  const [tomorrow, setTomorrow] = useState(startTomorrow && hasTomorrow)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [onClose])

  const dayKey = tomorrow ? tomorrowKey : todayKey
  const hours = tomorrow ? tomorrowHours : todayHours
  const text = useMemo(
    () =>
      dayKey && hours
        ? buildScheduleText({ lang, queue, dayKey, hours, update: snapshot.update })
        : '',
    [lang, queue, dayKey, hours, snapshot.update],
  )

  const { share: shareMsg, busy: sharing } = useShareMessage(text || undefined, Boolean(text))

  const flash = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  const share = () => {
    haptic('light')
    shareMsg(() => {
      debugLog('share schedule: dialog fallback')
      shareViaDialog(text, link)
    })
  }

  const copy = async () => {
    haptic('light')
    flash((await copyText(link ? `${text}\n\n${link}` : text)) ? t('sched.copied') : t('perk.copyFail'))
  }

  const sendCard = async () => {
    if (busy) return
    setBusy(true)
    haptic('light')
    try {
      await api('/api/share/send-card', { method: 'POST', body: json({ lang, text }) })
      haptic('medium')
      flash(t('perk.cardSent'))
    } catch (e) {
      debugLog(`send schedule failed: ${e instanceof Error ? e.message : String(e)}`)
      flash(t('perk.cardFail'))
    } finally {
      setBusy(false)
    }
  }

  const Chip = ({ active, disabled, onClick, children }: { active: boolean; disabled?: boolean; onClick: () => void; children: React.ReactNode }) => (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'rounded-full border px-4 py-1.5 text-sm transition-colors disabled:opacity-40',
        active ? 'border-on bg-on text-on-foreground' : 'bg-card hover:bg-muted',
      )}
    >
      {children}
    </button>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center" role="presentation">
      <button type="button" aria-label={t('sheet.close')} onClick={onClose} className="absolute inset-0 bg-black/50 backdrop-blur-sm animate-in fade-in" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-sched-title"
        className="relative max-h-[92dvh] w-full max-w-md overflow-y-auto rounded-t-3xl border-t bg-card p-5 pb-[max(env(safe-area-inset-bottom),1.25rem)] shadow-2xl animate-in slide-in-from-bottom duration-300"
      >
        <div className="mx-auto mb-3 h-1.5 w-12 rounded-full bg-muted" aria-hidden="true" />
        <div className="flex items-center justify-between">
          <h2 id="share-sched-title" className="font-display text-2xl font-bold">
            {t('sched.sheetTitle')}
          </h2>
          <button type="button" onClick={onClose} className="grid size-9 place-items-center rounded-full hover:bg-muted" aria-label={t('sheet.close')}>
            <X className="size-5" aria-hidden="true" />
          </button>
        </div>

        <div className="mt-3 flex gap-2">
          <Chip active={!tomorrow} disabled={!hasToday} onClick={() => setTomorrow(false)}>
            {t('hist.today')}
          </Chip>
          <Chip active={tomorrow} disabled={!hasTomorrow} onClick={() => setTomorrow(true)}>
            {t('hist.tomorrow')}
          </Chip>
        </div>

        <p className="mt-4 text-xs text-muted-foreground">{t('sched.preview')}</p>
        <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap rounded-2xl border bg-muted/40 p-3 text-sm leading-relaxed">
          {text || t('grid.emptyToday')}
        </pre>

        <div className="mt-4 grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={share}
            disabled={!text || sharing}
            className="flex items-center justify-center gap-1.5 rounded-full bg-on py-2.5 font-medium text-on-foreground disabled:opacity-60"
          >
            {sharing ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Send className="size-4" aria-hidden="true" />}
            {t('perk.share')}
          </button>
          <button
            type="button"
            onClick={copy}
            disabled={!text}
            className="flex items-center justify-center gap-1.5 rounded-full border py-2.5 font-medium hover:bg-muted disabled:opacity-50"
          >
            <Copy className="size-4" aria-hidden="true" />
            {t('perk.copy')}
          </button>
        </div>
        <button
          type="button"
          onClick={sendCard}
          disabled={!text || busy}
          className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-full border border-on/50 py-2.5 text-sm font-medium text-on hover:bg-on-soft disabled:opacity-50"
        >
          {busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <ImagePlus className="size-4" aria-hidden="true" />}
          {t('perk.sendCard')}
        </button>
        {toast && (
          <p role="status" className="mt-3 text-center text-sm text-on">
            {toast}
          </p>
        )}
      </div>
    </div>
  )
}
