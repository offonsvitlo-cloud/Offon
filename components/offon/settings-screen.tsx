'use client'

import { useState } from 'react'
import { Heart, Moon, MoonStar, Star } from 'lucide-react'
import { api, getTelegram, haptic, json, type Bootstrap } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useActions, useExtras } from '@/hooks/use-app-data'
import { ReferralCard } from './perks'
import { Switch } from './switch'

interface SettingsScreenProps {
  data: Bootstrap
  dark: boolean
  onToggleDark: (v: boolean) => void
}

export function SettingsScreen({ data, dark, onToggleDark }: SettingsScreenProps) {
  const { updateSettings, demo } = useActions()
  const extras = useExtras()
  const [message, setMessage] = useState<string | null>(null)
  const s = data.subscriber

  const openInvoice = async (path: string, body?: unknown) => {
    setMessage(null)
    if (demo) {
      setMessage('Оплата зірками доступна лише в Telegram')
      return
    }
    try {
      const res = await api<{ invoiceUrl: string }>(path, { method: 'POST', body: body ? json(body) : undefined })
      const tg = getTelegram()
      if (tg?.openInvoice) {
        tg.openInvoice(res.invoiceUrl, (status) => {
          if (status === 'paid') {
            haptic('heavy')
            setMessage('Дякуємо! Оплату зараховано.')
            extras.mutate()
          }
        })
      } else window.open(res.invoiceUrl, '_blank', 'noopener')
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Не вдалося створити рахунок')
    }
  }

  const pro = extras.data?.pro

  return (
    <div className="space-y-4">
      <h1 className="font-display text-3xl font-bold">Налаштування</h1>

      <section className="divide-y rounded-3xl border bg-card shadow-sm">
        <Row icon={<Moon className="size-5" aria-hidden="true" />} title="Темна тема">
          <Switch checked={dark} onCheckedChange={onToggleDark} label="Темна тема" />
        </Row>
        <Row
          icon={<MoonStar className="size-5" aria-hidden="true" />}
          title="Тихі години"
          subtitle="Без звуку сповіщень у цей час"
        >
          <Switch
            checked={s.quietHoursEnabled}
            onCheckedChange={(v) => updateSettings({ quietHoursEnabled: v })}
            label="Тихі години"
          />
        </Row>
        {s.quietHoursEnabled && (
          <div className="flex items-center gap-3 px-4 py-3">
            <label className="flex flex-1 flex-col gap-1 text-sm text-muted-foreground">
              Від
              <input
                type="time"
                value={s.quietStart}
                onChange={(e) => updateSettings({ quietStart: e.target.value })}
                className="h-10 rounded-xl border border-input bg-background px-3 text-foreground"
              />
            </label>
            <label className="flex flex-1 flex-col gap-1 text-sm text-muted-foreground">
              До
              <input
                type="time"
                value={s.quietEnd}
                onChange={(e) => updateSettings({ quietEnd: e.target.value })}
                className="h-10 rounded-xl border border-input bg-background px-3 text-foreground"
              />
            </label>
          </div>
        )}
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <span className="text-sm">Мова сповіщень бота</span>
          <div className="flex rounded-xl bg-muted p-0.5 text-sm" role="radiogroup" aria-label="Мова">
            {(['uk', 'ru'] as const).map((l) => (
              <button
                key={l}
                type="button"
                role="radio"
                aria-checked={s.language === l}
                onClick={() => updateSettings({ language: l })}
                className={cn('rounded-lg px-3 py-1', s.language === l ? 'bg-card font-medium shadow-sm' : 'text-muted-foreground')}
              >
                {l === 'uk' ? 'Українська' : 'Русский'}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section
        className="glow-card relative overflow-hidden rounded-3xl border bg-card p-4"
        style={{ ['--glow' as string]: 'var(--warn)' }}
      >
        <div className="flex items-start gap-3">
          <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-warn text-warn-foreground">
            <Star className="size-5" fill="currentColor" aria-hidden="true" />
          </span>
          <div>
            <p className="font-display text-xl font-bold">{pro?.active ? 'Pro активний' : 'offonsvitlo Pro'}</p>
            <p className="text-sm text-muted-foreground">
              {pro?.active && pro.until
                ? `Діє до ${new Date(pro.until).toLocaleDateString('uk-UA')}`
                : `До ${pro?.addresses ?? 5} адрес і без реклами · ${pro?.price ?? 100} ⭐ / ${pro?.periodDays ?? 30} днів`}
            </p>
          </div>
        </div>
        {!pro?.active && (
          <button
            type="button"
            onClick={() => openInvoice('/api/billing/create-pro-invoice')}
            className="mt-3 w-full rounded-full bg-warn py-2.5 font-medium text-warn-foreground"
          >
            Оформити Pro
          </button>
        )}
      </section>

      <section className="rounded-3xl border bg-card p-4 shadow-sm">
        <p className="flex items-center gap-2 font-medium">
          <Heart className="size-4 text-destructive" fill="currentColor" aria-hidden="true" />
          Підтримати проєкт
        </p>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {(extras.data?.donate.amounts ?? [10, 50, 100]).map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => openInvoice('/api/billing/create-donate-invoice', { stars: n })}
              className="rounded-xl border py-2 font-medium tabular-nums hover:bg-muted"
            >
              {n} ⭐
            </button>
          ))}
        </div>
        {extras.data?.donate.total ? (
          <p className="mt-2 text-xs text-muted-foreground">Ви вже підтримали на {extras.data.donate.total} ⭐ — дякуємо!</p>
        ) : null}
      </section>

      <ReferralCard />

      {message && (
        <p role="status" className="rounded-2xl bg-muted p-3 text-center text-sm">
          {message}
        </p>
      )}

      <p className="pb-2 text-center text-xs text-muted-foreground">
        Дані: ДТЕК Дніпровські електромережі · оновлено {data.snapshot.update ?? '—'}
      </p>
    </div>
  )
}

function Row({
  icon,
  title,
  subtitle,
  children,
}: {
  icon: React.ReactNode
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <span className="text-muted-foreground">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
      </div>
      {children}
    </div>
  )
}
