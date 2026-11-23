'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { debugLog } from '@/lib/ads'
import { api, getTelegram, json } from '@/lib/api'
import { useLang } from './use-app-data'

export function shareMessageSupported() {
  const tg = getTelegram()
  return Boolean(tg?.initData && tg.shareMessage && tg.isVersionAtLeast?.('8.0'))
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/**
 * Отправка друзьям сообщения «картинка + текст + кнопка» (Telegram.WebApp.shareMessage).
 * Сообщение готовится на сервере заранее; если на момент нажатия оно ещё не готово, кнопка
 * ЖДЁТ его (со спиннером), а не молчит. Если подготовить не удалось, вызывается fallback.
 */
export function useShareMessage(text: string | undefined, enabled = true) {
  const lang = useLang()
  const key = `${lang}|${text ?? ''}`
  const prepared = useRef<{ key: string; id: string; exp: number } | null>(null)
  const inflight = useRef<{ key: string; p: Promise<string | null> } | null>(null)
  const [busy, setBusy] = useState(false)

  const prepare = useCallback((): Promise<string | null> => {
    if (!shareMessageSupported()) return Promise.resolve(null)
    const cur = prepared.current
    if (cur && cur.key === key && cur.exp * 1000 > Date.now() + 60_000) return Promise.resolve(cur.id)
    if (inflight.current?.key === key) return inflight.current.p
    const p = (async () => {
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const res = await api<{ id?: string | null; expiresAt?: number; reason?: string; detail?: string }>(
            '/api/share/prepare',
            { method: 'POST', body: json({ lang, text }) },
          )
          if (res.id) {
            prepared.current = { key, id: res.id, exp: res.expiresAt ?? Math.floor(Date.now() / 1000) + 600 }
            return res.id
          }
          debugLog(`prepare: ${res.reason ?? '?'} ${res.detail ?? ''}`)
          if (res.reason === 'NOT_TELEGRAM' || res.reason === 'NO_BOT') break
        } catch (e) {
          debugLog(`prepare error: ${e instanceof Error ? e.message : String(e)}`)
        }
        await sleep(700)
      }
      return null
    })().finally(() => {
      if (inflight.current?.key === key) inflight.current = null
    })
    inflight.current = { key, p }
    return p
  }, [key, lang, text])

  // готовим заранее (с паузой, чтобы не дёргать сервер при быстром переключении дней)
  useEffect(() => {
    if (!enabled) return
    const id = setTimeout(() => {
      prepare()
    }, text === undefined ? 0 : 400)
    return () => clearTimeout(id)
  }, [prepare, enabled, text])

  const share = useCallback(
    async (fallback: () => void) => {
      if (busy) return
      setBusy(true)
      try {
        const id = await prepare()
        const tg = getTelegram()
        if (id && tg?.shareMessage) {
          const onFail = (data?: unknown) => {
            tg.offEvent?.('shareMessageFailed', onFail)
            const err = (data as { error?: string } | undefined)?.error
            debugLog(`shareMessageFailed: ${err ?? '?'}`)
            if (err !== 'USER_DECLINED') fallback()
          }
          tg.onEvent?.('shareMessageFailed', onFail)
          setTimeout(() => tg.offEvent?.('shareMessageFailed', onFail), 30_000)
          try {
            debugLog('shareMessage(prepared)')
            tg.shareMessage(id)
            prepared.current = null // на каждую отправку готовим новое сообщение
            setTimeout(() => {
              prepare()
            }, 1500)
            return
          } catch (e) {
            debugLog(`shareMessage threw: ${e instanceof Error ? e.message : String(e)}`)
            tg.offEvent?.('shareMessageFailed', onFail)
          }
        } else {
          debugLog('no prepared message, using fallback')
        }
        fallback()
      } finally {
        setBusy(false)
      }
    },
    [busy, prepare],
  )

  return { share, busy }
}
