export type BusinessType =
  | "pharmacy"
  | "coffee"
  | "fastfood"
  | "grocery"
  | "apparel"
  | "services";

export type StepStatus = {
  key: string;
  label: string;
  status: "pending" | "running" | "done" | "error";
  detail?: string | null;
  provider?: string | null;
  latency_ms?: number | null;
};

export type AnalysisResponse = {
  request_id: string;
  status: "pending" | "processing" | "completed" | "failed";
  city: string;
  business_type: BusinessType;
  lat: number;
  lng: number;
  result?: {
    verdict?: "recommend" | "acceptable" | "avoid";
    score?: {
      overall_score: number;
      foot_traffic_estimate: string;
      competition_level: string;
      visibility_score: number;
      infrastructure_score: number;
      accessibility_score: number;
      confidence: number;
      key_risks: string[];
      key_strengths: string[];
    };
    geo_context?: {
      address: { display_name: string; city: string; district?: string | null };
    };
    competitors: Array<{
      name: string;
      category: string;
      lat?: number | null;
      lng?: number | null;
      distance_m: number;
    }>;
    traffic?: {
      level: string;
      score: number;
      drivers: string[];
    };
    optimization?: {
      lat: number;
      lng: number;
      improvement_percent: number;
      distance_meters: number;
      reason: string;
    } | null;
    reasoning?: string | null;
    steps: StepStatus[];
    processing_time_ms?: number | null;
  };
};

export const MAJOR_CITIES = [
  { name: "Москва", lat: 55.7558, lng: 37.6173 },
  { name: "Санкт-Петербург", lat: 59.9343, lng: 30.3351 },
  { name: "Екатеринбург", lat: 56.8389, lng: 60.6057 },
  { name: "Казань", lat: 55.7961, lng: 49.1064 }
] as const;

export type CityOption = (typeof MAJOR_CITIES)[number];

const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function reverseGeocode(lat: number, lng: number) {
  const params = new URLSearchParams({ lat: String(lat), lng: String(lng) });
  return fetchJson<{ display_name: string }>(`/api/v1/geo/reverse?${params}`);
}

export async function createAnalysis(payload: {
  lat: number;
  lng: number;
  city: string;
  business_type: BusinessType;
}) {
  return fetchJson<AnalysisResponse>("/api/v1/analysis/analyze", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function getAnalysis(requestId: string) {
  return fetchJson<AnalysisResponse>(`/api/v1/analysis/${requestId}`);
}
