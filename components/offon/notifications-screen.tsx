'use client'

import { useState } from 'react'
import { BellRing, LoaderCircle, Send } from 'lucide-react'
import type { Bootstrap } from '@/lib/api'
import { getTelegram, haptic } from '@/lib/api'
import { useActions } from '@/hooks/use-app-data'
import { AlertItem } from './alert-item'

export function NotificationsScreen({ data }: { data: Bootstrap }) {
  const { testAlert } = useActions()
  const [sending, setSending] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const linked = Boolean(data.subscriber.telegramChatId)
  const bot = data.botStatus?.botUsername

  const onTest = async () => {
    setSending(true)
    setResult(null)
    try {
      const res = await testAlert()
      haptic('medium')
      setResult(res.deliveredTelegram ? 'Тестове сповіщення надіслано в Telegram' : 'Подію створено, але в Telegram не доставлено')
    } catch (e) {
      setResult(e instanceof Error ? e.message : 'Помилка')
    } finally {
      setSending(false)
    }
  }

  const openBot = () => {
    if (!bot) return
    const url = `https://t.me/${bot}?start=app`
    const tg = getTelegram()
    if (tg?.openTelegramLink) tg.openTelegramLink(url)
    else window.open(url, '_blank', 'noopener')
  }

  return (
    <div className="space-y-4">
      <h1 className="font-display text-3xl font-bold">Повідомлення</h1>

      <section className="rounded-3xl border bg-card p-4 shadow-sm">
        <div className="flex items-start gap-3">
          <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-on/15 text-on">
            <BellRing className="size-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <p className="font-medium">{linked ? 'Сповіщення в Telegram увімкнено' : 'Telegram не підключено'}</p>
            <p className="text-sm text-muted-foreground">
              {linked
                ? 'Попередимо перед вимкненням та увімкненням світла за вашими адресами.'
                : 'Натисніть /start у боті, щоб отримувати особисті сповіщення.'}
            </p>
          </div>
        </div>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={onTest}
            disabled={sending}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-full bg-on py-2.5 font-medium text-on-foreground disabled:opacity-60"
          >
            {sending ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Send className="size-4" aria-hidden="true" />}
            Тест сповіщення
          </button>
          {!linked && bot && (
            <button type="button" onClick={openBot} className="flex-1 rounded-full border py-2.5 font-medium">
              Відкрити бота
            </button>
          )}
        </div>
        {result && (
          <p role="status" className="mt-2 text-sm text-muted-foreground">
            {result}
          </p>
        )}
      </section>

      <section aria-labelledby="events-title">
        <h2 id="events-title" className="mb-2 text-sm text-muted-foreground">
          Останні події ({data.alerts.length})
        </h2>
        {data.alerts.length ? (
          <ul className="space-y-2">
            {data.alerts.map((a) => (
              <AlertItem key={a.id} alert={a} />
            ))}
          </ul>
        ) : (
          <p className="rounded-3xl border border-dashed p-8 text-center text-muted-foreground">Подій поки немає</p>
        )}
      </section>
    </div>
  )
}
