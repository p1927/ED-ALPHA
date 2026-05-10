import type { Metadata } from "next"
import { DemoVideoPage } from "@/components/demo-video-page"

export const metadata: Metadata = {
  title: "ED-ALPHA Demo Video",
  description: "Timed ED-ALPHA walkthrough with optional generated narration.",
}

export default function Page() {
  return <DemoVideoPage />
}
