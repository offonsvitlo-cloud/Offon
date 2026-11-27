'use client'

import { useEffect, useState } from 'react'
import { AdminApp } from '@/components/admin/admin-app'
import { AppShell } from '@/components/offon/app-shell'
import { api, getTelegram } from '@/lib/api'

/**
 * Один и тот же сайт: пользовательское приложение или админ-панель.
 * Админ-режим включается, если (1) в адресе есть ?mode=admin или путь /admin, либо
 * (2) сервер подтверждает, что Mini App открыт из админ-бота (по подписи Telegram), даже если адрес «потерял» параметры.
 */
export function RootEntry() {
  const [mode, setMode] = useState<'pending' | 'app' | 'admin'>('pending')

  useEffect(() => {
    const url = new URL(window.location.href)
    if (url.searchParams.get('mode') === 'admin' || /\/admin\/?$/.test(url.pathname)) {
      setMode('admin')
      return
    }
    const tg = getTelegram()
    if (!tg?.initData) {
      setMode('app')
      return
    }
    // не задерживаем обычных пользователей: если сервер отвечает медленно, открываем приложение
    let done = false
    const finish = (m: 'app' | 'admin') => {
      if (done) return
      done = true
      clearTimeout(timer)
      setMode(m)
    }
    const timer = setTimeout(() => finish('app'), 1200)
    api<{ kind?: string }>('/api/bot-kind')
      .then((r) => finish(r.kind === 'admin' ? 'admin' : 'app'))
      .catch(() => finish('app'))
    return () => clearTimeout(timer)
  }, [])

  if (mode === 'pending') return <div className="min-h-dvh bg-background" />
  return mode === 'admin' ? <AdminApp /> : <AppShell />
}
