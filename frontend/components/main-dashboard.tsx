"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  MAJOR_CITIES,
  AnalysisResponse,
  BusinessType,
  CityOption,
  createAnalysis,
  getAnalysis,
  reverseGeocode
} from "@/lib/api";

const BUSINESS_TYPES: Array<{ value: BusinessType; label: string }> = [
  { value: "pharmacy", label: "💊 Аптека" },
  { value: "coffee", label: "☕ Кофейня" },
  { value: "fastfood", label: "🍔 Фастфуд" },
  { value: "grocery", label: "🛒 Продукты" },
  { value: "apparel", label: "👕 Одежда" },
  { value: "services", label: "✂️ Услуги" }
];

function verdictMeta(verdict?: string) {
  if (verdict === "recommend") return { emoji: "🟢", label: "Рекомендуем", tone: "positive" };
  if (verdict === "acceptable") return { emoji: "🟡", label: "Допустимо", tone: "warning" };
  return { emoji: "🔴", label: "Не рекомендуем", tone: "danger" };
}

export function MainDashboard() {
  const [selectedCity, setSelectedCity] = useState<CityOption>(MAJOR_CITIES[0]);
  const [businessType, setBusinessType] = useState<BusinessType>("pharmacy");
  const [selectedPoint, setSelectedPoint] = useState<{ x: number; y: number; lat: number; lng: number } | null>(null);
  const [address, setAddress] = useState("Выберите точку на карте");
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!analysis || analysis.status !== "processing") return;
    const timer = window.setInterval(async () => {
      const fresh = await getAnalysis(analysis.request_id);
      setAnalysis(fresh);
      if (fresh.status !== "processing") {
        window.clearInterval(timer);
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, [analysis]);

  async function handleMapClick(event: React.MouseEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width;
    const y = (event.clientY - bounds.top) / bounds.height;
    const lat = Number((selectedCity.lat + (0.5 - y) * 0.035).toFixed(6));
    const lng = Number((selectedCity.lng + (x - 0.5) * 0.055).toFixed(6));
    setSelectedPoint({ x, y, lat, lng });
    setAnalysis(null);
    try {
      const result = await reverseGeocode(lat, lng);
      setAddress(result.display_name);
    } catch {
      setAddress(`${selectedCity.name} · ${lat}, ${lng}`);
    }
  }

  async function handleAnalyze() {
    if (!selectedPoint) return;
    setLoading(true);
    try {
      const created = await createAnalysis({
        lat: selectedPoint.lat,
        lng: selectedPoint.lng,
        city: selectedCity.name,
        business_type: businessType
      });
      setAnalysis(created);
    } finally {
      setLoading(false);
    }
  }

  const verdict = verdictMeta(analysis?.result?.verdict);
  const score = analysis?.result?.score;

  return (
    <main className="shell">
      <div className="hero">
        <div>
          <p className="eyebrow">GeoVerdict.AI</p>
          <h1>Вердикт по локации без тяжелой геоаналитики</h1>
          <p className="lead">
            Выберите город, поставьте точку и получите оценку трафика, конкуренции и
            объяснимую рекомендацию по запуску.
          </p>
        </div>
        <div className="hero-chip">PoC for retail site selection</div>
      </div>

      <div className="app-grid">
        <section className="map-panel" onClick={handleMapClick}>
          <div className="map-skin">
            <div className="street h-1" />
            <div className="street h-2" />
            <div className="street v-1" />
            <div className="street v-2" />
            <div className="street v-3" />
            <div className="block b-1" />
            <div className="block b-2" />
            <div className="block b-3" />
            <div className="block b-4" />
            <div className="heat heat-a" />
            <div className="heat heat-b" />
            <div className="heat heat-c" />

            {selectedPoint ? (
              <>
                <div
                  className="radius-ring"
                  style={{ left: `${selectedPoint.x * 100}%`, top: `${selectedPoint.y * 100}%` }}
                />
                <div
                  className="pin primary-pin"
                  style={{ left: `${selectedPoint.x * 100}%`, top: `${selectedPoint.y * 100}%` }}
                />
              </>
            ) : null}

            {analysis?.result?.optimization && selectedPoint ? (
              <div
                className="pin optimizer-pin"
                style={{
                  left: `${Math.min(88, selectedPoint.x * 100 + 10)}%`,
                  top: `${Math.max(18, selectedPoint.y * 100 - 8)}%`
                }}
              >
                <span>+{analysis.result.optimization.improvement_percent}%</span>
              </div>
            ) : null}

            {(analysis?.result?.competitors ?? []).slice(0, 4).map((competitor, index) => (
              <div
                className="pin competitor-pin"
                key={`${competitor.name}-${index}`}
                style={{
                  left: `${28 + index * 11}%`,
                  top: `${58 - index * 7}%`
                }}
              >
                {competitor.name.slice(0, 1)}
              </div>
            ))}
          </div>

          <div className="map-overlay top-left">
            {MAJOR_CITIES.map((city) => (
              <button
                key={city.name}
                className={city.name === selectedCity.name ? "city-pill active" : "city-pill"}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelectedCity(city);
                  setSelectedPoint(null);
                  setAddress("Выберите точку на карте");
                  setAnalysis(null);
                }}
                type="button"
              >
                {city.name}
              </button>
            ))}
          </div>

          <div className="map-overlay bottom-left">
            <span>Трафик</span>
            <div className="legend-bar" />
            <span>высокий</span>
          </div>
        </section>

        <aside className="sidebar">
          <div className="panel">
            <p className="section-title">Выбранная точка</p>
            <div className="address-card">
              <strong>{selectedCity.name}</strong>
              <p>{address}</p>
              {selectedPoint ? (
                <small>
                  {selectedPoint.lat}° N, {selectedPoint.lng}° E
                </small>
              ) : null}
            </div>
          </div>

          <div className="panel">
            <p className="section-title">Тип бизнеса</p>
            <div className="type-grid">
              {BUSINESS_TYPES.map((item) => (
                <button
                  key={item.value}
                  className={item.value === businessType ? "type-btn active" : "type-btn"}
                  onClick={() => setBusinessType(item.value)}
                  type="button"
                >
                  {item.label}
                </button>
              ))}
            </div>
            <button className="analyze-btn" disabled={!selectedPoint || loading} onClick={handleAnalyze} type="button">
              {loading ? "Запуск..." : "Анализировать локацию"}
            </button>
          </div>

          <div className="panel">
            <p className="section-title">Агенты</p>
            <div className="steps">
              {(analysis?.result?.steps ?? []).map((step) => (
                <div className="step-row" key={step.key}>
                  <span className={`step-dot ${step.status}`} />
                  <div>
                    <strong>{step.label}</strong>
                    <p>{step.detail ?? "Ожидает запуска"}</p>
                  </div>
                </div>
              ))}
              {!analysis ? <p className="muted">Шаги появятся после старта анализа.</p> : null}
            </div>
          </div>

          {score ? (
            <div className={`panel verdict-card ${verdict.tone}`}>
              <div className="verdict-head">
                <div>
                  <p className="verdict-line">
                    <span>{verdict.emoji}</span>
                    <strong>{verdict.label}</strong>
                  </p>
                  <small>Confidence {Math.round(score.confidence * 100)}%</small>
                </div>
                <div className="big-score">{Math.round(score.overall_score)}</div>
              </div>

              <div className="metric-list">
                <div className="metric-row">
                  <span>Пешеходный поток</span>
                  <strong>{score.foot_traffic_estimate}</strong>
                </div>
                <div className="metric-row">
                  <span>Видимость</span>
                  <strong>{Math.round(score.visibility_score)}</strong>
                </div>
                <div className="metric-row">
                  <span>Инфраструктура</span>
                  <strong>{Math.round(score.infrastructure_score)}</strong>
                </div>
                <div className="metric-row">
                  <span>Доступность</span>
                  <strong>{Math.round(score.accessibility_score)}</strong>
                </div>
              </div>

              <div className="tags">
                {score.key_strengths.map((item) => (
                  <span className="tag strength" key={item}>
                    {item}
                  </span>
                ))}
                {score.key_risks.map((item) => (
                  <span className="tag risk" key={item}>
                    {item}
                  </span>
                ))}
              </div>

              {analysis?.request_id ? (
                <Link className="detail-link" href={`/analysis/${analysis.request_id}`}>
                  Открыть детальный разбор
                </Link>
              ) : null}
            </div>
          ) : null}

          {analysis?.result?.optimization ? (
            <div className="panel optimization-card">
              <p className="section-title">Лучшая точка рядом</p>
              <strong>+{analysis.result.optimization.improvement_percent}% к потенциалу</strong>
              <p>
                {analysis.result.optimization.distance_meters} м · {analysis.result.optimization.reason}
              </p>
            </div>
          ) : null}
        </aside>
      </div>
    </main>
  );
}
