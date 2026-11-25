'use client'

import { Crown, Lock } from 'lucide-react'
import type { Subscriber } from '@/lib/api'
import { addressesWord, daysWord } from '@/lib/i18n'
import { haptic } from '@/lib/api'
import { useExtras, useT } from '@/hooks/use-app-data'
import { ReferralCard, TrialCard } from './perks'

interface RestoreOptionsProps {
  subscriber: Subscriber
  now: Date
  labels: string[]
  onOpenPro: () => void
}

/** Что показываем вместо графика, когда адрес «заморожен»: три способа его вернуть. */
export function RestoreOptions({ subscriber, now, labels, onOpenPro }: RestoreOptionsProps) {
  const { t, lang } = useT()
  const extras = useExtras()
  const pro = extras.data?.pro
  const n = pro?.addresses ?? 5
  const days = pro?.periodDays ?? 30

  return (
    <section className="space-y-3" aria-label={t('lock.aria')}>
      <div className="rounded-3xl border border-warn/50 bg-warn/10 p-4">
        <div className="flex items-start gap-3">
          <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-warn/20 text-warn-foreground dark:text-warn">
            <Lock className="size-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="font-display text-lg font-bold">
              {labels.length === 1 ? t('lock.homeTitle', { label: labels[0] }) : t('lock.manyTitle', { n: labels.length })}
            </p>
            <p className="text-sm text-muted-foreground">{t('lock.desc')}</p>
          </div>
        </div>
      </div>

      {!subscriber.proActive && (
        <div className="rounded-3xl border bg-card p-4 shadow-sm">
          <div className="flex items-start gap-3">
            <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-warn/20 text-warn-foreground dark:text-warn">
              <Crown className="size-5" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="font-display text-lg font-bold">{t('set.proTitle')}</p>
              <p className="text-sm text-muted-foreground">
                {t('set.proDesc', {
                  n,
                  addr: addressesWord(lang, n),
                  p: pro?.price ?? 100,
                  days: `${days} ${daysWord(lang, days)}`,
                })}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              haptic('light')
              onOpenPro()
            }}
            className="mt-3 w-full rounded-full bg-warn py-2.5 font-medium text-warn-foreground"
          >
            {t('set.proBuy')}
          </button>
        </div>
      )}

      <TrialCard subscriber={subscriber} now={now} />
      <ReferralCard />
    </section>
  )
}
