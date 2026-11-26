'use client'

import { useEffect, useState } from 'react'
import { AdminApp } from '@/components/admin/admin-app'
import { AppShell } from '@/components/offon/app-shell'

/** Один и тот же сайт: пользовательский режим или админ-панель (?mode=admin для админ-бота). */
export function RootEntry() {
  const [mode, setMode] = useState<'pending' | 'app' | 'admin'>('pending')
  useEffect(() => {
    setMode(new URLSearchParams(window.location.search).get('mode') === 'admin' ? 'admin' : 'app')
  }, [])
  if (mode === 'pending') return <div className="min-h-dvh bg-background" />
  return mode === 'admin' ? <AdminApp /> : <AppShell />
}
