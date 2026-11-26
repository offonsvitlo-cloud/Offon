export interface AdminStats {
  users: number
  webUsers: number
  newToday: number
  new7d: number
  active24h: number
  active7d: number
  active30d: number
  telegramLinked: number
  proActive: number
  proExpiring3d: number
  rewardActive: number
  refBonusActive: number
  addresses: number
  usersWithoutAddress: number
  pausedAddresses: number
  referrals: number
  adsToday: number
  adsTotal: number
  starsPaid: number
  remindersPending: number
  topQueues: { queue: string; n: number }[]
  languages: Record<string, number>
  signups: { date: string; n: number }[]
  topInviters: { id: string; name: string; n: number }[]
}

export interface AdminUserRow {
  id: string
  name: string
  username?: string | null
  language: string
  addresses: number
  proActive: boolean
  proUntil?: string | null
  lastSeen?: string | null
  createdAt?: string | null
  invited: number
  referredBy?: { id: string; name: string } | null
  telegram: boolean
  visits: number
}

export interface AdminUserDetail extends AdminUserRow {
  maxAddresses: number
  addressList: { label?: string; city?: string; queue?: string; enabled: boolean; locked: boolean }[]
  extraAddressSlots: number
  rewardSlotExpiresAt?: string | null
  refBonusUntil?: string | null
  adsToday: number
  adsWatched: number
  starsPaid: number
  donatedStars: number
  giftedProDays: number
  reminders: number
  invitedList: {
    id: string
    name: string
    username?: string | null
    createdAt?: string | null
    lastSeen?: string | null
    addresses: number
    proActive: boolean
  }[]
  activity: { t: string; e: string; days?: number; amount?: number; delta?: number; queue?: string }[]
}

export interface AdminLogItem {
  t: string
  admin?: number
  adminName?: string
  target: string
  action: string
  detail?: string
}

export const MAIN_BOT = 'Offonsvitlo_bot'
