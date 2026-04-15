import { AnalysisDetail } from "@/components/analysis-detail";

export default async function AnalysisPage({
  params
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <AnalysisDetail requestId={id} />;
}
