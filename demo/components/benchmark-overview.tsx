"use client"

import { BarChart3, Database, FileCheck2, Target, Upload } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

const overviewItems = [
  {
    icon: Target,
    label: "Objective",
    tourId: "benchmark-overview-objective",
    text: "Predict investment-relevant Form 8-K events after a prediction date.",
  },
  {
    icon: Database,
    label: "Inputs",
    tourId: "benchmark-overview-inputs",
    text: "Use only pre-event public data, including SEC company metadata and recent news.",
  },
  {
    icon: Upload,
    label: "Submission shape",
    tourId: "benchmark-overview-submission-shape",
    text: "Submit company-level scores or a ranked list of CIKs.",
  },
  {
    icon: FileCheck2,
    label: "Ground truth",
    tourId: "benchmark-overview-ground-truth",
    text: "Tracked Form 8-K item codes define later event matches.",
  },
  {
    icon: BarChart3,
    label: "Evaluation",
    tourId: "benchmark-overview-evaluation",
    text: "Score the Top-K ranking with precision, recall, F1, and true positives.",
  },
]

export function BenchmarkOverview() {
  return (
    <Card className="metric-card shadow-lg border-border" data-tour-id="benchmark-overview">
      <CardHeader className="border-b border-border/50">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="space-y-2">
            <CardTitle className="text-xl font-bold">Benchmark Overview</CardTitle>
            <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
              ED-ALPHA turns public news and filing data into a reproducible event-prediction benchmark.
              This demo asks whether news available before September 2025 can rank companies that later
              disclose tracked Form 8-K events.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">Open data</Badge>
            <Badge variant="outline">Future events</Badge>
            <Badge variant="outline">Top-K ranking</Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-6">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5" data-tour-id="benchmark-overview-boxes">
          {overviewItems.map((item) => {
            const Icon = item.icon

            return (
              <div key={item.label} className="rounded-lg border border-border bg-card/70 p-4" data-tour-id={item.tourId}>
                <div className="mb-3 flex items-center gap-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <Icon className="h-4 w-4" />
                  </div>
                  <div className="text-sm font-semibold">{item.label}</div>
                </div>
                <p className="text-sm leading-6 text-muted-foreground">{item.text}</p>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
