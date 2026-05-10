"use client"

import { Activity, HelpCircle } from "lucide-react"
import { Button } from "@/components/ui/button"

interface HeaderProps {
  onTutorialOpen?: () => void
}

export function Header({ onTutorialOpen }: HeaderProps) {
  return (
    <header className="border-b border-border bg-gradient-to-r from-card via-card to-secondary/50 backdrop-blur-sm">
      <div className="container mx-auto px-6 py-5 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3" data-tour-id="product-identity">
          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-primary to-primary/60 flex items-center justify-center">
            <Activity className="w-6 h-6 text-primary-foreground" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-foreground tracking-tight">ED-ALPHA</h1>
            <p className="text-xs text-muted-foreground font-mono">Open Event Prediction Benchmark</p>
          </div>
        </div>
        {onTutorialOpen && (
          <Button variant="outline" size="sm" onClick={onTutorialOpen}>
            <HelpCircle className="w-4 h-4" />
            Tutorial
          </Button>
        )}
      </div>
    </header>
  )
}
