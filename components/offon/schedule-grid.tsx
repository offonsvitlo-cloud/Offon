'use client'

import { CircleHelp, Clock, Zap, ZapOff } from 'lucide-react'
import { hourKind, type HourKind, type SlotValue } from '@/lib/schedule'
import { cn } from '@/lib/utils'
import { Switch } from './switch'

interface ScheduleGridProps {
  hours: Record<string, SlotValue> | undefined
  queueName: string
  showTomorrow: boolean
  onToggleTomorrow: (v: boolean) => void
  clockLabel: string
  minutesOfDay: number
  dark: boolean
  onToggleDark: (v: boolean) => void
}

const KIND_STYLE: Record<HourKind, string> = {
  on: 'bg-on text-on-foreground border-on shadow-[inset_0_1px_0_oklch(1_0_0/25%),0_2px_6px_-2px_var(--on)]',
  off: 'bg-off text-off-foreground border-off shadow-[inset_0_1px_0_oklch(1_0_0/12%)]',
  'off-first': 'bg-warn text-warn-foreground border-warn',
  'off-second': 'bg-warn text-warn-foreground border-warn',
  maybe: 'bg-warn/10 text-warn-foreground dark:text-warn border-warn',
  unknown: 'bg-muted text-muted-foreground border-border',
}

const KIND_TEXT: Record<HourKind, string> = {
  on: 'є',
  off: 'Вимк.',
  'off-first': 'до :30',
  'off-second': 'з :30',
  maybe: 'Можл.',
  unknown: '—',
}

const KIND_LABEL: Record<HourKind, string> = {
  on: 'світло є',
  off: 'відключення',
  'off-first': 'відключення перші 30 хвилин',
  'off-second': 'відключення другі 30 хвилин',
  maybe: 'можливе відключення',
  unknown: 'немає даних',
}

function KindIcon({ kind }: { kind: HourKind }) {
  const cls = 'size-3.5 shrink-0'
  if (kind === 'on') return <Zap className={cls} fill="currentColor" aria-hidden="true" />
  if (kind === 'off') return <ZapOff className={cn(cls, 'opacity-70')} aria-hidden="true" />
  if (kind === 'unknown') return <CircleHelp className={cls} aria-hidden="true" />
  if (kind === 'maybe') return <Zap className={cls} aria-hidden="true" />
  return <Clock className={cls} aria-hidden="true" />
}

export function ScheduleGrid({
  hours,
  queueName,
  showTomorrow,
  onToggleTomorrow,
  clockLabel,
  minutesOfDay,
  dark,
  onToggleDark,
}: ScheduleGridProps) {
  const currentHour = Math.floor(minutesOfDay / 60)
  const hasData = hours && Object.keys(hours).length > 0
  const dayProgress = (minutesOfDay / 1440) * 100

  return (
    <section aria-labelledby="schedule-title" className="rounded-3xl border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <h2 id="schedule-title" className="font-display text-xl font-medium tracking-wide uppercase">
          Графік відключень
        </h2>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className={cn(showTomorrow && 'font-medium text-foreground')}>Завтра</span>
          <Switch checked={showTomorrow} onCheckedChange={onToggleTomorrow} label="Показати графік на завтра" />
        </label>
      </div>

      <div className="mt-1 flex items-end justify-between gap-2 text-sm text-muted-foreground">
        <span>
          24-годинна шкала ({queueName}){showTomorrow ? ' · завтра' : ''}
        </span>
        {!showTomorrow && <span className="font-medium text-foreground tabular-nums">{clockLabel}</span>}
      </div>
      {!showTomorrow && (
        <div className="relative mt-1.5 h-1 rounded-full bg-muted" aria-hidden="true">
          <div className="h-full rounded-full bg-foreground/50" style={{ width: `${dayProgress}%` }} />
          <span
            className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-foreground shadow"
            style={{ left: `${dayProgress}%` }}
          />
        </div>
      )}

      {hasData ? (
        <ol className="mt-3 grid grid-cols-6 gap-1.5" aria-label={`Графік по годинах, ${queueName}`}>
          {Array.from({ length: 24 }, (_, h) => {
            const kind = hourKind(hours?.[String(h + 1)])
            const isNow = !showTomorrow && h === currentHour
            const time = `${String(h).padStart(2, '0')}:00`
            return (
              <li
                key={h}
                aria-label={`${time} — ${KIND_LABEL[kind]}${isNow ? ', зараз' : ''}`}
                aria-current={isNow ? 'time' : undefined}
                className={cn(
                  'relative flex min-h-14 flex-col justify-between rounded-[10px] border px-1.5 py-1.5 text-[11px] leading-tight transition-transform',
                  KIND_STYLE[kind],
                  isNow && 'now-ring z-10 scale-[1.04]',
                  !showTomorrow && h < currentHour && 'opacity-55',
                )}
              >
                <span className="flex items-center justify-between gap-0.5 font-medium tabular-nums">
                  {time}
                  <KindIcon kind={kind} />
                </span>
                <span className="truncate text-xs font-medium">{KIND_TEXT[kind]}</span>
              </li>
            )
          })}
        </ol>
      ) : (
        <p className="mt-4 rounded-2xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          {showTomorrow ? 'Графік на завтра ще не опубліковано' : 'Графік на сьогодні ще не опубліковано'}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground" aria-label="Легенда">
          <li className="flex items-center gap-1">
            <span className="size-2.5 rounded-sm bg-on" aria-hidden="true" />є
          </li>
          <li className="flex items-center gap-1">
            <span className="size-2.5 rounded-sm bg-off" aria-hidden="true" />
            вимк.
          </li>
          <li className="flex items-center gap-1">
            <span className="size-2.5 rounded-sm bg-warn" aria-hidden="true" />
            пів години
          </li>
        </ul>
        <label className="flex items-center gap-2 text-sm">
          Світло/Темно
          <Switch checked={dark} onCheckedChange={onToggleDark} label="Темна тема" />
        </label>
      </div>
    </section>
  )
}
