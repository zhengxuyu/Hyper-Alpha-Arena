import { useEffect, useState } from 'react'
import { Moon, Sun, DollarSign } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import toast from 'react-hot-toast'
import { switchGlobalTradeMode, syncAllAccountsFromKraken } from '@/lib/api'

interface HeaderProps {
  title?: string
  onTradeModeChanged?: () => void
}

export default function Header({ title = 'Hyper Alpha Arena', onTradeModeChanged }: HeaderProps) {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'dark'
    return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
  })

  const [tradeMode, setTradeMode] = useState<'paper' | 'real'>(() => {
    if (typeof window === 'undefined') return 'paper'
    return (window.localStorage.getItem('trade_mode') as 'paper' | 'real') || 'paper'
  })
  const [syncing, setSyncing] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined') return
    const stored = window.localStorage.getItem('theme')
    if (stored === 'light' || stored === 'dark') {
      setTheme(stored)
    } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      setTheme('dark')
    }
  }, [])

  useEffect(() => {
    if (typeof document === 'undefined') return
    const root = document.documentElement
    if (theme === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('theme', theme)
    }
  }, [theme])

  const toggleTheme = () => {
    setTheme(prev => (prev === 'dark' ? 'light' : 'dark'))
  }

  const handleTradeModeChange = async (newMode: 'paper' | 'real') => {
    if (syncing) return

    try {
      setSyncing(true)

      if (newMode === 'real') {
        // Confirm with user before switching to real trading
        const confirmed = window.confirm(
          '⚠️ Switching to Real Trading mode will use your actual Kraken account for trading.\n\n' +
          'This will sync all accounts with real balances, positions, and orders from Kraken.\n\n' +
          'Are you sure you want to continue?'
        )
        if (!confirmed) {
          return
        }
      }

      toast.loading(`Switching to ${newMode === 'real' ? 'Real Trading' : 'Paper Trading'} mode...`)

      await switchGlobalTradeMode(newMode)

      setTradeMode(newMode)
      window.localStorage.setItem('trade_mode', newMode)

      toast.dismiss()
      toast.success(
        newMode === 'real'
          ? 'Switched to Real Trading mode. Account information synced from Kraken'
          : 'Switched to Paper Trading mode'
      )

      // Refresh data immediately - request fresh snapshot from WebSocket
      if (onTradeModeChanged) {
        onTradeModeChanged()
      }

      // For real trading mode, wait longer for Kraken sync to complete
      // For paper trading, we can reload faster
      const reloadDelay = newMode === 'real' ? 3000 : 1500

      setTimeout(() => {
        window.location.reload()
      }, reloadDelay)

    } catch (error) {
      toast.dismiss()
      toast.error(`Failed to switch mode: ${error instanceof Error ? error.message : 'Unknown error'}`)
    } finally {
      setSyncing(false)
    }
  }

  const handleSyncNow = async () => {
    if (tradeMode !== 'real') {
      toast.error('Kraken sync is only available in Real Trading mode')
      return
    }

    try {
      setSyncing(true)
      toast.loading('Syncing account data from Kraken...')

      await syncAllAccountsFromKraken()

      toast.dismiss()
      toast.success('Account data synced successfully')

      if (onTradeModeChanged) {
        onTradeModeChanged()
      }

      // Wait for sync to complete, then refresh page to show updated data
      setTimeout(() => {
        window.location.reload()
      }, 1500)

    } catch (error) {
      toast.dismiss()
      toast.error(`Sync failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
    } finally {
      setSyncing(false)
    }
  }

  return (
    <header className="w-full border-b bg-background/50 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="w-full py-2 px-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <img src="/static/logo_app.png" alt="Logo" className="h-8 w-8 object-contain" />
          <h1 className="text-xl font-bold">{title}</h1>
        </div>

        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <DollarSign className="h-4 w-4 text-muted-foreground" />
            <Select
              value={tradeMode}
              onValueChange={(value) => handleTradeModeChange(value as 'paper' | 'real')}
              disabled={syncing}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="paper">Paper Trading</SelectItem>
                <SelectItem value="real">Real Trading</SelectItem>
              </SelectContent>
            </Select>
            {tradeMode === 'real' && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleSyncNow}
                disabled={syncing}
                className="text-xs"
              >
                {syncing ? 'Syncing...' : 'Sync Now'}
              </Button>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleTheme}
            aria-label="Toggle theme"
          >
            {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </header>
  )
}
