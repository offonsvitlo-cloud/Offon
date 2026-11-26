'use client'

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Crown, LoaderCircle, Lock, RefreshCw, Search, ShieldCheck, X } from 'lucide-react'
import { api, ApiError, getTelegram, haptic, json } from '@/lib/api'
import type { AdminLogItem, AdminStats, AdminUserDetail, AdminUserRow } from '@/lib/admin'
import { MAIN_BOT } from '@/lib/admin'
import {
  clientPref,
  LANGS,
  LOCALE,
  queueLabel,
  resolveLang,
  setUiLang,
  subscribeLang,
  translate,
  type Key,
  type Lang,
} from '@/lib/i18n'
import { cn } from '@/lib/utils'

/* ---------- язык панели (без запросов к основному API) ---------- */

function useAdminT() {
  const pref = useSyncExternalStore(subscribeLang, clientPref, () => '')
  const lang: Lang = resolveLang(pref, 'ru')
  const t = useCallback((key: Key, vars?: Record<string, string | number>) => translate(lang, key, vars), [lang])
  return { t, lang }
}

type TFn = ReturnType<typeof useAdminT>['t']

const fmtDateTime = (iso: string | null | undefined, lang: Lang) =>
  iso
    ? new Date(iso).toLocaleString(LOCALE[lang], {
        timeZone: 'Europe/Kyiv',
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—'

const fmtDate = (iso: string | null | undefined, lang: Lang) =>
  iso ? new Date(iso).toLocaleDateString(LOCALE[lang], { timeZone: 'Europe/Kyiv', day: '2-digit', month: '2-digit', year: 'numeric' }) : '—'

function ago(iso: string | null | undefined, t: TFn) {
  if (!iso) return t('admin.never')
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (min < 2) return t('admin.justNow')
  const v = min < 60 ? `${min} ${t('unit.m')}` : min < 1440 ? `${Math.floor(min / 60)} ${t('unit.h')}` : `${Math.floor(min / 1440)} ${t('unit.d')}`
  return t('admin.ago', { v })
}

function eventLabel(e: string, t: TFn) {
  const key = `admin.ev.${e}` as Key
  const txt = t(key)
  return txt === key ? e : txt
}

/* ---------- каркас ---------- */

export function AdminApp() {
  const { t, lang } = useAdminT()
  const [state, setState] = useState<'loading' | 'ok' | 'denied' | 'notg'>('loading')
  const [name, setName] = useState('')
  const [tab, setTab] = useState<'stats' | 'users' | 'log'>('stats')
  const [openId, setOpenId] = useState<string | null>(null)

  useEffect(() => {
    const tg = getTelegram()
    tg?.ready?.()
    tg?.expand?.()
    try {
      tg?.setHeaderColor?.('#000000')
      tg?.setBackgroundColor?.('#000000')
    } catch {}
    document.documentElement.classList.add('dark')
    document.documentElement.lang = lang
    if (!tg?.initData) {
      setState('notg')
      return
    }
    api<{ name?: string }>('/api/admin/me')
      .then((r) => {
        setName(r.name ?? '')
        setState('ok')
      })
      .catch((e) => setState(e instanceof ApiError && e.status === 403 ? 'denied' : 'denied'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // обычный пользователь: переадресация на основной бот
  useEffect(() => {
    if (state !== 'denied') return
    const id = setTimeout(() => openMainBot(), 1500)
    return () => clearTimeout(id)
  }, [state])

  if (state === 'loading') {
    return (
      <div className="grid min-h-dvh place-items-center bg-background text-muted-foreground">
        <LoaderCircle className="size-8 animate-spin" aria-hidden="true" />
      </div>
    )
  }

  if (state !== 'ok') {
    return (
      <main className="mx-auto grid min-h-dvh max-w-md place-items-center bg-background px-6 text-center">
        <div>
          <Lock className="mx-auto size-10 text-muted-foreground" aria-hidden="true" />
          {state === 'denied' ? (
            <>
              <h1 className="mt-4 font-display text-2xl font-bold">{t('admin.denied.title')}</h1>
              <p className="mt-2 text-muted-foreground">{t('admin.denied.desc', { bot: MAIN_BOT })}</p>
              <button
                type="button"
                onClick={openMainBot}
                className="mt-5 rounded-full bg-on px-6 py-3 font-medium text-on-foreground"
              >
                {t('admin.denied.btn', { bot: MAIN_BOT })}
              </button>
            </>
          ) : (
            <p className="mt-4 text-muted-foreground">{t('admin.needTg')}</p>
          )}
        </div>
      </main>
    )
  }

  return (
    <div className="mx-auto min-h-dvh max-w-md bg-background px-4 pb-10 pt-[max(env(safe-area-inset-top),0.75rem)] text-foreground">
      <header className="flex items-center justify-between gap-3 py-2">
        <div className="flex items-center gap-2">
          <span className="grid size-9 place-items-center rounded-xl bg-on text-on-foreground">
            <ShieldCheck className="size-5" aria-hidden="true" />
          </span>
          <div>
            <h1 className="font-display text-xl font-bold leading-tight">{t('admin.title')}</h1>
            {name && <p className="text-xs text-muted-foreground">{name}</p>}
          </div>
        </div>
        <div role="radiogroup" aria-label="language" className="flex rounded-full border bg-card p-0.5 text-xs font-semibold">
          {LANGS.map((l) => (
            <button
              key={l}
              type="button"
              role="radio"
              aria-checked={lang === l}
              onClick={() => setUiLang(l)}
              className={cn('rounded-full px-2.5 py-1 uppercase', lang === l ? 'bg-on text-on-foreground' : 'text-muted-foreground')}
            >
              {l}
            </button>
          ))}
        </div>
      </header>

      <nav className="mt-2 grid grid-cols-3 gap-1 rounded-2xl bg-muted p-1" aria-label="tabs">
        {(['stats', 'users', 'log'] as const).map((id) => (
          <button
            key={id}
            type="button"
            aria-pressed={tab === id}
            onClick={() => {
              haptic('light')
              setTab(id)
            }}
            className={cn(
              'rounded-xl py-2 text-sm font-medium transition-colors',
              tab === id ? 'bg-card shadow-sm' : 'text-muted-foreground',
            )}
          >
            {t(`admin.tab.${id}` as Key)}
          </button>
        ))}
      </nav>

      <main className="mt-4">
        {tab === 'stats' && <StatsTab t={t} onOpenUser={setOpenId} />}
        {tab === 'users' && <UsersTab t={t} lang={lang} onOpenUser={setOpenId} />}
        {tab === 'log' && <LogTab t={t} lang={lang} onOpenUser={setOpenId} />}
      </main>

      {openId && <UserSheet key={openId} id={openId} t={t} lang={lang} onClose={() => setOpenId(null)} onOpenUser={setOpenId} />}
    </div>
  )
}

function openMainBot() {
  const tg = getTelegram()
  const url = `https://t.me/${MAIN_BOT}`
  try {
    if (tg?.openTelegramLink) {
      tg.openTelegramLink(url)
      setTimeout(() => tg.close?.(), 400)
      return
    }
  } catch {}
  window.open(url, '_blank', 'noopener')
}

/* ---------- обзор ---------- */

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-2xl border bg-card p-3">
      <p className="font-display text-2xl font-bold tabular-nums">{value}</p>
      <p className="text-xs text-muted-foreground">{label}</p>
      {hint && <p className="text-[11px] text-muted-foreground/80">{hint}</p>}
    </div>
  )
}

function BarRow({ label, value, max }: { label: string; value: number; max: number }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="w-16 shrink-0 truncate tabular-nums">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-on" style={{ width: `${max ? (value / max) * 100 : 0}%` }} />
      </div>
      <span className="w-8 shrink-0 text-right tabular-nums text-muted-foreground">{value}</span>
    </div>
  )
}

function StatsTab({ t, onOpenUser }: { t: TFn; onOpenUser: (id: string) => void }) {
  const [s, setS] = useState<AdminStats | null>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    setBusy(true)
    try {
      setS(await api<AdminStats>('/api/admin/stats'))
    } catch {}
    setBusy(false)
  }, [])
  useEffect(() => {
    load()
  }, [load])

  if (!s) return <LoaderCircle className="mx-auto mt-10 size-7 animate-spin text-muted-foreground" aria-hidden="true" />
  return <StatsView s={s} busy={busy} onRefresh={load} t={t} onOpenUser={onOpenUser} />
}

export function StatsView({
  s,
  busy,
  onRefresh,
  t,
  onOpenUser,
}: {
  s: AdminStats
  busy: boolean
  onRefresh: () => void
  t: TFn
  onOpenUser: (id: string) => void
}) {
  const maxSign = Math.max(1, ...s.signups.map((x) => x.n))
  const maxQ = Math.max(1, ...s.topQueues.map((x) => x.n))
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={onRefresh}
          disabled={busy}
          className="flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs text-muted-foreground"
        >
          <RefreshCw className={cn('size-3.5', busy && 'animate-spin')} aria-hidden="true" />
          {t('app.retry')}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Stat label={t('admin.s.users')} value={s.users} />
        <Stat label={t('admin.s.new')} value={s.newToday} hint={`${t('admin.s.new7')}: ${s.new7d}`} />
        <Stat label={t('admin.s.a24')} value={s.active24h} />
        <Stat label={t('admin.s.a7')} value={s.active7d} hint={`${t('admin.s.a30')}: ${s.active30d}`} />
        <Stat label={t('admin.s.pro')} value={s.proActive} hint={`${t('admin.s.proExp')}: ${s.proExpiring3d}`} />
        <Stat label={t('admin.s.tg')} value={s.telegramLinked} />
        <Stat label={t('admin.s.addr')} value={s.addresses} hint={`${t('admin.s.noaddr')}: ${s.usersWithoutAddress}`} />
        <Stat label={t('admin.s.paused')} value={s.pausedAddresses} />
        <Stat label={t('admin.s.refs')} value={s.referrals} />
        <Stat label={t('admin.s.stars')} value={s.starsPaid} />
        <Stat label={t('admin.s.adsToday')} value={s.adsToday} hint={`${t('admin.s.adsTotal')}: ${s.adsTotal}`} />
        <Stat label={t('admin.s.rem')} value={s.remindersPending} hint={`${t('admin.s.web')}: ${s.webUsers}`} />
      </div>

      <section className="rounded-2xl border bg-card p-3">
        <h2 className="mb-2 text-sm font-medium">{t('admin.signups')}</h2>
        <div className="flex h-24 items-end gap-1" role="img" aria-label={t('admin.signups')}>
          {s.signups.map((d) => (
            <div key={d.date} className="flex h-full flex-1 flex-col justify-end" title={`${d.date}: ${d.n}`}>
              <div className="rounded-t bg-on" style={{ height: `${(d.n / maxSign) * 100}%`, minHeight: d.n ? 3 : 1 }} />
            </div>
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
          <span>{s.signups[0]?.date.slice(5)}</span>
          <span>{s.signups[s.signups.length - 1]?.date.slice(5)}</span>
        </div>
      </section>

      {s.topQueues.length > 0 && (
        <section className="space-y-1.5 rounded-2xl border bg-card p-3">
          <h2 className="mb-1 text-sm font-medium">{t('admin.queues')}</h2>
          {s.topQueues.map((q) => (
            <BarRow key={q.queue} label={q.queue.replace(/^GPV/, '')} value={q.n} max={maxQ} />
          ))}
        </section>
      )}

      <section className="space-y-1.5 rounded-2xl border bg-card p-3">
        <h2 className="mb-1 text-sm font-medium">{t('admin.langs')}</h2>
        {Object.entries(s.languages).map(([l, n]) => (
          <BarRow key={l} label={l.toUpperCase()} value={n} max={Math.max(1, ...Object.values(s.languages))} />
        ))}
      </section>

      {s.topInviters.length > 0 && (
        <section className="rounded-2xl border bg-card p-3">
          <h2 className="mb-1 text-sm font-medium">{t('admin.inviters')}</h2>
          <ul>
            {s.topInviters.map((u) => (
              <li key={u.id}>
                <button
                  type="button"
                  onClick={() => onOpenUser(u.id)}
                  className="flex w-full items-center justify-between rounded-lg px-1 py-2 text-left text-sm hover:bg-muted"
                >
                  <span className="truncate">{u.name}</span>
                  <span className="tabular-nums text-muted-foreground">{u.n}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

/* ---------- пользователи ---------- */

const FILTERS = ['all', 'pro', 'active', 'inviters', 'noaddr'] as const
const FILTER_API: Record<(typeof FILTERS)[number], string> = {
  all: 'all',
  pro: 'pro',
  active: 'active24',
  inviters: 'inviters',
  noaddr: 'noaddr',
}
const SORTS = ['seen', 'created', 'invited', 'addresses'] as const

function UsersTab({ t, lang, onOpenUser }: { t: TFn; lang: Lang; onOpenUser: (id: string) => void }) {
  const [q, setQ] = useState('')
  const [dq, setDq] = useState('')
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('all')
  const [sort, setSort] = useState<(typeof SORTS)[number]>('seen')
  const [items, setItems] = useState<AdminUserRow[]>([])
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const reqId = useRef(0)

  useEffect(() => {
    const id = setTimeout(() => setDq(q.trim()), 350)
    return () => clearTimeout(id)
  }, [q])

  const load = useCallback(
    async (offset: number) => {
      const my = ++reqId.current
      setBusy(true)
      try {
        const res = await api<{ total: number; items: AdminUserRow[] }>(
          `/api/admin/users?q=${encodeURIComponent(dq)}&filter=${FILTER_API[filter]}&sort=${sort}&offset=${offset}&limit=30`,
        )
        if (my !== reqId.current) return
        setTotal(res.total)
        setItems((prev) => (offset === 0 ? res.items : [...prev, ...res.items]))
      } catch {}
      if (my === reqId.current) setBusy(false)
    },
    [dq, filter, sort],
  )

  useEffect(() => {
    load(0)
  }, [load])

  return (
    <div className="space-y-3">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t('admin.search')}
          autoComplete="off"
          className="h-11 w-full rounded-xl border border-input bg-background pr-3 pl-9 text-base outline-none focus:border-on focus:ring-2 focus:ring-on/30"
        />
      </div>
      <div className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
            className={cn(
              'rounded-full border px-3 py-1 text-xs',
              filter === f ? 'border-on bg-on text-on-foreground' : 'bg-card text-muted-foreground',
            )}
          >
            {t(`admin.f.${f}` as Key)}
          </button>
        ))}
      </div>
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{t('admin.total', { n: total })}</span>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as (typeof SORTS)[number])}
          className="rounded-lg border bg-card px-2 py-1"
          aria-label="sort"
        >
          {SORTS.map((s) => (
            <option key={s} value={s}>
              {t(`admin.sort.${s}` as Key)}
            </option>
          ))}
        </select>
      </div>

      <ul className="space-y-1.5">
        {items.map((u) => (
          <li key={u.id}>
            <button
              type="button"
              onClick={() => onOpenUser(u.id)}
              className="flex w-full items-center gap-3 rounded-2xl border bg-card p-3 text-left active:scale-[0.99]"
            >
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1.5 truncate font-medium">
                  {u.name}
                  {u.proActive && <Crown className="size-3.5 shrink-0 text-warn" aria-label="Pro" />}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {u.username ? `@${u.username} · ` : ''}
                  {u.id.replace('tg:', '')}
                </p>
                <p className="text-xs text-muted-foreground">
                  {ago(u.lastSeen, t)} · {t('admin.row.addr', { n: u.addresses })}
                  {u.invited ? ` · ${t('admin.row.inv', { n: u.invited })}` : ''}
                </p>
              </div>
              <span className="shrink-0 text-xs uppercase text-muted-foreground">{u.language}</span>
            </button>
          </li>
        ))}
      </ul>
      {!busy && items.length === 0 && <p className="py-8 text-center text-muted-foreground">{t('admin.empty')}</p>}
      {busy && <LoaderCircle className="mx-auto size-6 animate-spin text-muted-foreground" aria-hidden="true" />}
      {!busy && items.length < total && (
        <button
          type="button"
          onClick={() => load(items.length)}
          className="w-full rounded-full border py-2.5 text-sm font-medium hover:bg-muted"
        >
          {t('admin.more')}
        </button>
      )}
      <span className="sr-only">{lang}</span>
    </div>
  )
}

/* ---------- журнал ---------- */

function LogTab({ t, lang, onOpenUser }: { t: TFn; lang: Lang; onOpenUser: (id: string) => void }) {
  const [items, setItems] = useState<AdminLogItem[] | null>(null)
  useEffect(() => {
    api<{ items: AdminLogItem[] }>('/api/admin/log')
      .then((r) => setItems(r.items))
      .catch(() => setItems([]))
  }, [])
  if (!items) return <LoaderCircle className="mx-auto mt-10 size-7 animate-spin text-muted-foreground" aria-hidden="true" />
  return (
    <section>
      <h2 className="mb-2 text-sm font-medium">{t('admin.log.title')}</h2>
      {items.length === 0 && <p className="py-8 text-center text-muted-foreground">{t('admin.log.empty')}</p>}
      <ul className="space-y-1.5">
        {items.map((it, i) => (
          <li key={`${it.t}-${i}`}>
            <button
              type="button"
              onClick={() => onOpenUser(it.target)}
              className="w-full rounded-2xl border bg-card p-3 text-left text-sm"
            >
              <p className="font-medium">
                {eventLabel(it.action === 'slots' ? 'admin_slots' : it.action, t)}
                {it.detail ? ` · ${it.detail}` : ''}
              </p>
              <p className="text-xs text-muted-foreground">
                {fmtDateTime(it.t, lang)} · {it.adminName ?? it.admin} → {it.target}
              </p>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

/* ---------- карточка пользователя ---------- */

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 text-sm">
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="text-right font-medium">{v}</dd>
    </div>
  )
}

function UserSheet({
  id,
  t,
  lang,
  onClose,
  onOpenUser,
}: {
  id: string
  t: TFn
  lang: Lang
  onClose: () => void
  onOpenUser: (id: string) => void
}) {
  const [u, setU] = useState<AdminUserDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    api<AdminUserDetail>(`/api/admin/user?id=${encodeURIComponent(id)}`)
      .then(setU)
      .catch(() => onClose())
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const flash = (m: string) => {
    setToast(m)
    setTimeout(() => setToast(null), 2500)
  }

  const act = async (path: string, body: Record<string, unknown>) => {
    setBusy(true)
    try {
      const res = await api<{ user: AdminUserDetail }>(path, { method: 'POST', body: json({ id, ...body }) })
      setU(res.user)
      haptic('medium')
      flash(t('admin.a.done'))
    } catch {
      flash(t('admin.a.fail'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center" role="presentation">
      <button type="button" aria-label="close" onClick={onClose} className="absolute inset-0 bg-black/60" />
      <div
        role="dialog"
        aria-modal="true"
        className="relative max-h-[92dvh] w-full max-w-md overflow-y-auto rounded-t-3xl border-t bg-card p-5 pb-[max(env(safe-area-inset-bottom),1.25rem)] shadow-2xl"
      >
        <div className="mx-auto mb-3 h-1.5 w-12 rounded-full bg-muted" aria-hidden="true" />
        {!u ? (
          <LoaderCircle className="mx-auto my-10 size-7 animate-spin text-muted-foreground" aria-hidden="true" />
        ) : (
          <UserBody u={u} t={t} lang={lang} busy={busy} toast={toast} onAct={act} onClose={onClose} onOpenUser={onOpenUser} />
        )}
      </div>
    </div>
  )
}

export function UserBody({
  u,
  t,
  lang,
  busy,
  toast,
  onAct,
  onClose,
  onOpenUser,
}: {
  u: AdminUserDetail
  t: TFn
  lang: Lang
  busy: boolean
  toast: string | null
  onAct: (path: string, body: Record<string, unknown>) => Promise<void>
  onClose: () => void
  onOpenUser: (id: string) => void
}) {
  const [days, setDays] = useState('')
  const [confirmRevoke, setConfirmRevoke] = useState(false)
  const gift = (d: number) => onAct('/api/admin/user/pro', { days: d })
  const customDays = Number.parseInt(days, 10)
  return (
    <>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <h2 className="flex items-center gap-2 truncate font-display text-2xl font-bold">
                  {u.name}
                  {u.proActive && <Crown className="size-5 shrink-0 text-warn" aria-label="Pro" />}
                </h2>
                <p className="truncate text-sm text-muted-foreground">
                  {u.username ? `@${u.username}` : ''} {u.id.replace('tg:', '')}
                </p>
              </div>
              <button type="button" onClick={onClose} className="grid size-9 shrink-0 place-items-center rounded-full hover:bg-muted" aria-label="close">
                <X className="size-5" aria-hidden="true" />
              </button>
            </div>

            <dl className="mt-3 divide-y rounded-2xl border bg-muted/30 px-3">
              <Row k={t('admin.d.created')} v={fmtDate(u.createdAt, lang)} />
              <Row k={t('admin.d.seen')} v={`${ago(u.lastSeen, t)} · ${fmtDateTime(u.lastSeen, lang)}`} />
              <Row k={t('admin.d.visits')} v={u.visits} />
              <Row k={t('admin.d.lang')} v={u.language.toUpperCase()} />
              <Row k={t('admin.d.tg')} v={u.telegram ? t('admin.d.yes') : t('admin.d.no')} />
              <Row k="Pro" v={u.proActive && u.proUntil ? t('admin.d.proUntil', { d: fmtDate(u.proUntil, lang) }) : t('admin.d.noPro')} />
              {u.giftedProDays > 0 && <Row k="🎁" v={t('admin.d.gifted', { n: u.giftedProDays })} />}
              {(u.starsPaid > 0 || u.donatedStars > 0) && <Row k="⭐" v={t('admin.d.stars', { n: u.starsPaid + u.donatedStars })} />}
              <Row k="▶" v={t('admin.d.ads', { a: u.adsToday, b: u.adsWatched })} />
              {u.extraAddressSlots > 0 && <Row k="＋" v={t('admin.d.slots', { n: u.extraAddressSlots })} />}
              {u.rewardSlotExpiresAt && <Row k="⏳" v={t('admin.d.bonus', { d: fmtDateTime(u.rewardSlotExpiresAt, lang) })} />}
              {u.refBonusUntil && <Row k="👥" v={t('admin.d.refBonus', { d: fmtDateTime(u.refBonusUntil, lang) })} />}
            </dl>

            <h3 className="mt-4 text-sm font-medium">{t('admin.d.addresses', { n: u.addressList.length, max: u.maxAddresses })}</h3>
            {u.addressList.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t('admin.d.none')}</p>
            ) : (
              <ul className="mt-1 space-y-1 text-sm">
                {u.addressList.map((a, i) => (
                  <li key={i} className="flex items-center justify-between gap-2 rounded-xl border px-3 py-2">
                    <span className="truncate">
                      {a.label} · {a.city}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {a.queue ? queueLabel(lang, a.queue) : ''}
                      {a.locked ? ` · ${t('admin.d.paused')}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            {u.referredBy && (
              <>
                <h3 className="mt-4 text-sm font-medium">{t('admin.d.invitedBy')}</h3>
                <button
                  type="button"
                  onClick={() => onOpenUser(u.referredBy!.id)}
                  className="mt-1 w-full rounded-xl border px-3 py-2 text-left text-sm hover:bg-muted"
                >
                  {u.referredBy.name}
                </button>
              </>
            )}

            <h3 className="mt-4 text-sm font-medium">{t('admin.d.invited', { n: u.invitedList.length })}</h3>
            {u.invitedList.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t('admin.d.none')}</p>
            ) : (
              <ul className="mt-1 space-y-1">
                {u.invitedList.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      onClick={() => onOpenUser(r.id)}
                      className="flex w-full items-center justify-between gap-2 rounded-xl border px-3 py-2 text-left text-sm hover:bg-muted"
                    >
                      <span className="flex min-w-0 items-center gap-1.5 truncate">
                        {r.name}
                        {r.proActive && <Crown className="size-3.5 shrink-0 text-warn" aria-label="Pro" />}
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {ago(r.lastSeen, t)} · {t('admin.row.addr', { n: r.addresses })}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <h3 className="mt-4 text-sm font-medium">{t('admin.d.activity')}</h3>
            {u.activity.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t('admin.d.none')}</p>
            ) : (
              <ul className="mt-1 space-y-1 text-sm">
                {u.activity.map((a, i) => (
                  <li key={`${a.t}-${i}`} className="flex items-baseline justify-between gap-3">
                    <span className="truncate">
                      {eventLabel(a.e, t)}
                      {a.days ? ` · +${a.days} ${t('unit.d')}` : ''}
                      {a.amount ? ` · ${a.amount} ⭐` : ''}
                      {a.delta ? ` · ${a.delta > 0 ? '+' : ''}${a.delta}` : ''}
                      {a.queue ? ` · ${queueLabel(lang, a.queue)}` : ''}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">{fmtDateTime(a.t, lang)}</span>
                  </li>
                ))}
              </ul>
            )}

            <h3 className="mt-5 text-sm font-medium">{t('admin.d.actions')}</h3>
            <div className="mt-2 rounded-2xl border p-3">
              <p className="mb-2 text-sm font-medium">{t('admin.a.gift')}</p>
              <div className="grid grid-cols-3 gap-2">
                {[1, 7, 30].map((d) => (
                  <button
                    key={d}
                    type="button"
                    disabled={busy}
                    onClick={() => gift(d)}
                    className="rounded-xl bg-on py-2 text-sm font-medium text-on-foreground disabled:opacity-50"
                  >
                    {t(`admin.a.d${d}` as Key)}
                  </button>
                ))}
              </div>
              <div className="mt-2 flex gap-2">
                <input
                  value={days}
                  onChange={(e) => setDays(e.target.value.replace(/\D/g, '').slice(0, 4))}
                  inputMode="numeric"
                  placeholder={t('admin.a.days')}
                  className="h-10 min-w-0 flex-1 rounded-xl border border-input bg-background px-3 text-base outline-none focus:border-on"
                />
                <button
                  type="button"
                  disabled={busy || !(customDays >= 1 && customDays <= 3650)}
                  onClick={() => {
                    gift(customDays)
                    setDays('')
                  }}
                  className="rounded-xl border border-on px-4 text-sm font-medium text-on disabled:opacity-40"
                >
                  {t('admin.a.give')}
                </button>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onAct('/api/admin/user/slots', { delta: 1 })}
                  className="rounded-xl border py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"
                >
                  {t('admin.a.slotPlus')}
                </button>
                <button
                  type="button"
                  disabled={busy || u.extraAddressSlots < 1}
                  onClick={() => onAct('/api/admin/user/slots', { delta: -1 })}
                  className="rounded-xl border py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"
                >
                  {t('admin.a.slotMinus')}
                </button>
              </div>
              {u.proActive && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => (confirmRevoke ? onAct('/api/admin/user/pro/revoke', {}).then(() => setConfirmRevoke(false)) : setConfirmRevoke(true))}
                  className="mt-3 w-full rounded-xl border border-destructive/50 py-2 text-sm font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
                >
                  {confirmRevoke ? t('admin.a.revokeConfirm') : t('admin.a.revoke')}
                </button>
              )}
            </div>
            {toast && (
              <p role="status" className="mt-3 text-center text-sm text-on">
                {toast}
              </p>
            )}
    </>
  )
}
