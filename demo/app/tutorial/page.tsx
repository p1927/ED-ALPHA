import type { Metadata } from "next"
import { DashboardPage } from "@/components/dashboard-page"

export const metadata: Metadata = {
  title: "ED-Alpha Tutorial",
  description: "Interactive ED-Alpha dashboard tutorial.",
}

export default function Page() {
  return <DashboardPage />
}
