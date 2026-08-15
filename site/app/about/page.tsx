import meta from "../../public/data/meta.json";
import corrections from "../../public/data/corrections.json";
import { LIMIT_TEXT } from "../../components/Limits";
import type { Correction } from "../../lib/types";

export const metadata = {
  title: "소개 — ARGUS",
  description: "ARGUS 가 무엇을 하는지, 어떤 데이터를 다루는지, 무엇을 못 하는지.",
};

/**
 * 🔴 **이걸 보러 온 사람이 하려는 일**: *"이 화면들을 어디까지 믿어도 되는지 판단한다."*
 *
 * 그래서 이 쪽의 절반이 **한계**다. 우리가 무엇을 만들었는지는 여기서도 쓰지 않는다 —
 * 사람이 알고 싶은 것은 만든 과정이 아니라 **데이터의 경계**다.
 */
export default function AboutPage() {
  const list = corrections as Correction[];
  const unresolved = list.filter((f) => !f.resolved).length;
  const changed = list.filter(
    (f) => f.resolved && f.amount_before != null && f.amount_after != null && f.amount_before !== f.amount_after,
  ).length;

  return (
    <main className="page">
      <div className="container narrow prose">
        <div className="head">
          <h1>소개</h1>
          <p>공시 하나를 읽는 게 아니라, 공시들 사이의 관계를 읽습니다.</p>
        </div>

        <h2>무엇을 하나</h2>
        <p>
          DART 에 흩어져 있는 공시 {meta.counts.disclosures.toLocaleString()}건을 회사 단위로 모으고, 계약 공시가
          나중에 정정됐는지 원공시와 이어 붙이고, 공시에 이름이 적힌 계약 상대를 모아 거래 관계로 만듭니다.
        </p>
        <p>
          <strong>근거 없는 답은 하지 않습니다.</strong> 공시에서 확인되지 않은 값은 비워 둡니다 — 0으로도
          어림수로도 채우지 않습니다.
        </p>

        <h2>데이터 범위</h2>
        <ul>
          <li>
            <strong>{meta.ranges.companies}개사</strong> — 국내 상장사 가운데 골라 둔 범위입니다.
          </li>
          <li>
            <strong>{meta.ranges.from.replace("-", ".")} ~ {meta.ranges.to.replace("-", ".")}</strong> 사이에 접수된
            공시 <strong>{meta.counts.disclosures.toLocaleString()}건</strong>
          </li>
          <li>
            문서군 셋 — <strong>거래소공시</strong>(단일판매·공급계약 등) · <strong>주요사항보고</strong> ·{" "}
            <strong>지분공시</strong>
          </li>
          <li>
            재무 요약은 <strong>정기공시</strong>(사업·분기보고서)의 「요약재무정보」 표에서 따로 옮깁니다 — 위
            건수에는 들어 있지 않습니다.
          </li>
          <li>
            그중 계약 공시 <strong>{meta.counts.contracts.toLocaleString()}건</strong> · 정정 공시{" "}
            <strong>{meta.counts.corrections.toLocaleString()}건</strong> · 공시에 이름이 적힌 계약 상대{" "}
            <strong>{meta.counts.partners}곳</strong>
          </li>
        </ul>
        <p>
          <strong>코퍼스가 고정입니다.</strong> 새 공시를 실시간으로 받아 오지 않습니다. 위 기간 밖의 공시는 이
          화면에 없습니다.
        </p>

        <h2>정정 추적이 하는 일</h2>
        <p>
          정정 공시는 자기가 무엇을 고쳤는지 <em>날짜와 서식명</em>으로만 가리킵니다. 같은 날 같은 서식이 여러 건이면
          후보가 여럿이 되고, 우리는 <strong>계약명까지 같은 후보만</strong> 원공시로 인정합니다. 후보가 하나뿐이어도
          계약명이 다르면 쓰지 않습니다 — <strong>참을 하나 놓치는 것이 거짓을 하나 싣는 것보다 낫습니다.</strong>
        </p>
        <p>
          그 결과 정정 {meta.counts.corrections.toLocaleString()}건 가운데 금액이 실제로 바뀐 것{" "}
          <strong>{changed.toLocaleString()}건</strong>을 확인했고,{" "}
          <strong>{unresolved.toLocaleString()}건은 원공시를 찾지 못했습니다.</strong> 찾지 못한 것도 목록에 그대로
          두고 사유를 적습니다.
        </p>

        <h2>🔴 못 하는 것</h2>
        <ul>
          <li>{LIMIT_TEXT.universe} 그 밖의 회사는 이 화면에 아예 없습니다.</li>
          <li>{LIMIT_TEXT.counterparty}</li>
          <li>
            {LIMIT_TEXT.correction} 지금 {unresolved.toLocaleString()}건이 그렇습니다.
          </li>
          <li>{LIMIT_TEXT.network}</li>
          <li>
            재무 요약은 <strong>{meta.ranges.companies}개사 중 {meta.counts.financials}곳</strong>만 확인했습니다.
            정기공시의 요약표에서 <strong>기간·단위·연결 여부가 모두 읽히는 경우에만</strong> 싣고, 하나라도 못
            읽으면 비웁니다 — 기간을 모르는 매출액은 뜻이 없기 때문입니다.
          </li>
        </ul>

        <h2>공시 원문은 어디서 보나</h2>
        <p>
          화면의 접수번호가 DART 의 문서 번호입니다. 원문은 DART 에서 보십시오 — 우리는 원문을 다시 싣지 않습니다.
          우리가 더할 수 있는 것은 <strong>공시들 사이의 관계</strong>뿐입니다.
        </p>
      </div>
    </main>
  );
}
