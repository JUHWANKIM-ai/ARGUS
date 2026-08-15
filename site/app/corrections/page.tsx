import corrections from "../../public/data/corrections.json";
import companies from "../../public/data/companies.json";
import meta from "../../public/data/meta.json";
import CorrectionBrowser from "../../components/CorrectionBrowser";
import Limits from "../../components/Limits";
import type { CompanySummary, Correction } from "../../lib/types";

export const metadata = {
  title: "정정 추적 — ARGUS",
  description:
    "계약 공시가 나중에 정정됐는지, 금액이 얼마에서 얼마로 바뀌었는지 원공시와 이어 붙여 보여 줍니다.",
};

/**
 * 🔴 **우리만의 것.** DART 에서 원공시만 보면 그것이 나중에 정정됐는지 알 수 없다.
 *
 * 🔴 **이걸 보러 온 사람이 하려는 일**: *"내가 아는 그 공시가 나중에 바뀌었는지 확인한다."*
 */
export default function CorrectionsPage() {
  const list = corrections as Correction[];
  const share = Math.round((meta.counts.corrections / meta.counts.contracts) * 10);

  const changed = list.filter(
    (f) => f.resolved && f.amount_before != null && f.amount_after != null && f.amount_before !== f.amount_after,
  ).length;
  const unresolved = list.filter((f) => !f.resolved).length;

  return (
    <main className="page">
      <div className="container">
        <div className="head">
          <h1>정정 추적</h1>
          <p>
            계약 공시 10건 중 {share}건이 나중에 정정됩니다. 원공시를 찾아 이어 붙이고{" "}
            <strong>금액이 얼마에서 얼마로 바뀌었는지</strong> 같은 줄에 씁니다.
          </p>
        </div>

        <dl className="stats" style={{ marginBottom: "var(--gap-6)" }}>
          <div className="stat">
            <dt>정정 공시</dt>
            <dd>
              {list.length.toLocaleString()}
              <small> 건</small>
            </dd>
          </div>
          <div className="stat">
            <dt>금액이 바뀐 것</dt>
            <dd style={{ color: "var(--tone-corrected)" }}>
              {changed.toLocaleString()}
              <small> 건</small>
            </dd>
          </div>
          <div className="stat">
            <dt>원공시를 못 찾은 것</dt>
            <dd style={{ color: "var(--tone-unknown)" }}>
              {unresolved.toLocaleString()}
              <small> 건</small>
            </dd>
          </div>
        </dl>

        {/*
          🔴 추적 실패를 목록에서 빼지 않는 이유를 화면에 적는다.
          감추면 목록이 실제보다 완전해 보이고, 사람은 "여기 없으니 정정이 없구나" 로 읽는다.
        */}
        <p className="muted" style={{ marginTop: "calc(-1 * var(--gap-4))", marginBottom: "var(--gap-5)" }}>
          원공시를 못 찾은 {unresolved.toLocaleString()}건도 목록에 그대로 둡니다. 대부분은 원공시가 우리 수집
          기간(2023.01) 이전이거나, 정정 공시에 원공시를 가리키는 항목이 없기 때문입니다 — 사유를 건마다 적었습니다.
        </p>

        <CorrectionBrowser corrections={list} companies={companies as CompanySummary[]} />

        <Limits kinds={["correction", "universe"]} />
      </div>
    </main>
  );
}
