import type { Metadata } from "next"
import { DemoVideoPage } from "@/components/demo-video-page"

export const metadata: Metadata = {
  title: "ED-Alpha Walkthrough",
  description: "Timed ED-Alpha HTML walkthrough with optional generated narration.",
}

export default function Page() {
  return <DemoVideoPage />
}
