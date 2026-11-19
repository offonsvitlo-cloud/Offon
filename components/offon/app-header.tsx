import { Zap } from 'lucide-react'

export function AppHeader({ title, demo }: { title?: string; demo?: boolean }) {
  return (
    <header className="flex items-center justify-between gap-3 px-4 pt-[max(env(safe-area-inset-top),0.75rem)] pb-2">
      <div className="flex items-center gap-2">
        <span className="grid size-8 place-items-center rounded-xl bg-on text-on-foreground shadow-[0_0_14px_-2px_var(--on)]">
          <Zap className="size-4" fill="currentColor" aria-hidden="true" />
        </span>
        <span className="font-display text-2xl font-bold tracking-tight">{title ?? 'offonsvitlo'}</span>
      </div>
      {demo ? (
        <span className="rounded-full border border-warn/50 bg-warn/15 px-2.5 py-1 text-[11px] font-semibold text-warn-foreground dark:text-warn">
          Демо-дані
        </span>
      ) : null}
    </header>
  )
}
