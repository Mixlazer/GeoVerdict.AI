"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AnalysisResponse, getAnalysis } from "@/lib/api";

export function AnalysisDetail({ requestId }: { requestId: string }) {
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);

  useEffect(() => {
    getAnalysis(requestId).then(setAnalysis);
  }, [requestId]);

  if (!analysis?.result?.score) {
    return (
      <main className="detail-shell">
        <p className="eyebrow">GeoVerdict.AI</p>
        <h1>Детальный анализ</h1>
        <p className="lead">Загружаем результат анализа {requestId}...</p>
      </main>
    );
  }

  const { result } = analysis;
  const score = result.score!;

  return (
    <main className="detail-shell">
      <Link className="back-link" href="/">
        ← Назад к карте
      </Link>
      <p className="eyebrow">Разбор точки</p>
      <h1>{result.geo_context?.address.display_name ?? analysis.city}</h1>
      <p className="lead">
        Вердикт: <strong>{result.verdict}</strong> · Время анализа {result.processing_time_ms} мс
      </p>

      <section className="detail-grid">
        <article className="detail-card">
          <h2>Итоговый скоринг</h2>
          <div className="detail-metrics">
            <div>
              <span>Overall</span>
              <strong>{Math.round(score.overall_score)}</strong>
            </div>
            <div>
              <span>Visibility</span>
              <strong>{Math.round(score.visibility_score)}</strong>
            </div>
            <div>
              <span>Infrastructure</span>
              <strong>{Math.round(score.infrastructure_score)}</strong>
            </div>
            <div>
              <span>Accessibility</span>
              <strong>{Math.round(score.accessibility_score)}</strong>
            </div>
          </div>
        </article>

        <article className="detail-card">
          <h2>Драйверы трафика</h2>
          <ul className="plain-list">
            {result.traffic?.drivers.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </article>

        <article className="detail-card">
          <h2>Конкуренты рядом</h2>
          <ul className="plain-list">
            {result.competitors.map((item) => (
              <li key={`${item.name}-${item.distance_m}`}>
                {item.name} · {Math.round(item.distance_m)} м
              </li>
            ))}
          </ul>
        </article>

        <article className="detail-card">
          <h2>Шаги пайплайна</h2>
          <ul className="plain-list">
            {result.steps.map((step) => (
              <li key={step.key}>
                {step.label} · {step.status}
                {step.provider ? ` · ${step.provider}` : ""}
              </li>
            ))}
          </ul>
        </article>

        <article className="detail-card full">
          <h2>Reasoning</h2>
          <p>{result.reasoning}</p>
        </article>
      </section>
    </main>
  );
}
