/**
 * `public/data/*.json` 의 모양. 🔴 **이 파일이 파이썬 빌드 스크립트와의 계약이다** —
 * `scripts/build_service_data.py` 가 필드를 늘리면 여기도 늘어야 하고,
 * 그 판단은 사람이 한다(빌드 스크립트의 필드 게이트가 같은 규율이다).
 */

export type Meta = {
  generated_at: string;
  ranges: { companies: number; from: string; to: string };
  counts: {
    disclosures: number;
    corrections: number;
    contracts: number;
    partners: number;
    /** 재무 요약이 **확인된** 회사 수. 70 보다 작다 — 못 뽑은 곳은 비워 둔다. */
    financials: number;
  };
};

export type CompanySummary = {
  slug: string;
  name: string;
  sector: string;
  n_disclosures: number;
  n_corrections: number;
  top_partners: string[];
};

export type Contract = {
  rcept_no: string;
  rcept_dt: string;
  title: string | null;
  amount: number | null;
  amount_type: string | null;
  counterparty: string | null;
  period_start: string | null;
  period_end: string | null;
  is_correction: boolean;
  doc_subtype: string | null;
};

export type Correction = {
  rcept_no: string;
  slug: string | null;
  corp_name: string;
  date: string;
  form_name: string | null;
  title: string | null;
  /** 🔴 `false` 면 원공시를 못 찾았다는 뜻이다. 감추지 않는다. */
  resolved: boolean;
  reason: string | null;
  origin: string | null;
  targets: string[];
  amount_before: number | null;
  amount_after: number | null;
};

export type Partner = {
  name: string;
  kind: string;
  /** 관계는 확인됐지만 건수를 셀 수 없는 경우가 있다 — 그때는 `null` 이다. */
  contracts: number | null;
};

export type Financials = {
  period: string;
  basis: string;
  revenue: number | null;
  operating_income: number | null;
  source_rcept_no: string;
} | null;

export type CompanyDetail = {
  slug: string;
  name: string;
  sector: string;
  n_disclosures: number;
  n_corrections: number;
  contracts: Contract[];
  corrections: Correction[];
  partners: Partner[];
  financials: Financials;
};

export type Hub = {
  name: string;
  canonical: string;
  kind: string;
  count: number;
  partners: string[];
};

export type Pair = { a: string; b: string; via: string[] };

export type Network = { hubs: Hub[]; pairs: Pair[] };
