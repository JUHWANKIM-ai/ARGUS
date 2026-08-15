"use client";

import { useState } from "react";
import type { Contract, Correction } from "../lib/types";
import { nameKey, shortName, won, ym, ymd } from "../lib/format";

type Props = {
  contracts: Contract[];
  /** 접수번호 → 그 공시의 정정 추적 결과. 있으면 금액 변화를 같은 줄에 쓴다. */
  correctionByRcept: Record<string, Correction>;
  slugByName: Record<string, string>;
  initial?: number;
};

/**
 * 계약 공시 표.
 *
 * 🔴 **정정된 행은 눈에 띄어야 한다.** 색만으로 구분하지 않는다 — 왼쪽 띠 · "정정" 표지 ·
 * 금액 변화 한 줄이 함께 붙는다. 색을 못 보는 사람도 표지와 글자로 안다.
 *
 * 🔴 **금액이 그대로인 정정도 그렇게 쓴다.** "정정" 만 보고 금액이 바뀌었다고
 * 지레짐작하는 것을 막는 것이 이 표의 일이다.
 */
export default function ContractTable({ contracts, correctionByRcept, slugByName, initial = 25 }: Props) {
  const [limit, setLimit] = useState(initial);
  const rows = [...contracts].sort((a, b) => b.rcept_dt.localeCompare(a.rcept_dt));
  const visible = rows.slice(0, limit);

  if (rows.length === 0) {
    return <p className="muted">이 기간에 계약 공시가 없습니다.</p>;
  }

  return (
    <>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>공시일</th>
              <th>계약</th>
              <th className="right">금액</th>
              <th>상대</th>
              <th>기간</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((c) => {
              const fix = correctionByRcept[c.rcept_no];
              const changed =
                fix && fix.amount_before != null && fix.amount_after != null && fix.amount_before !== fix.amount_after;
              const same =
                fix && fix.amount_before != null && fix.amount_after != null && fix.amount_before === fix.amount_after;
              const partnerSlug = c.counterparty ? slugByName[nameKey(c.counterparty)] : undefined;
              const period = [ym(c.period_start), ym(c.period_end)].filter(Boolean);

              return (
                <tr key={c.rcept_no} className={c.is_correction ? "is-corrected" : undefined}>
                  <td className="num">
                    {ymd(c.rcept_dt)}
                    {c.is_correction ? (
                      <>
                        <br />
                        <span className="tag-corrected">정정</span>
                      </>
                    ) : null}
                  </td>
                  <td>
                    {c.title ?? c.doc_subtype ?? "-"}
                    {fix && !fix.resolved ? (
                      <span className="cell-title">원공시를 찾지 못해 무엇이 바뀌었는지 알 수 없습니다</span>
                    ) : null}
                  </td>
                  <td className="num right">
                    {won(c.amount) ?? <span className="tag-unknown">금액 없음</span>}
                    {changed ? (
                      <span className="amount-change">
                        <s>{won(fix.amount_before)}</s> → {won(fix.amount_after)}
                      </span>
                    ) : null}
                    {same ? <span className="cell-title">금액은 그대로</span> : null}
                  </td>
                  <td>
                    {c.counterparty ? (
                      partnerSlug ? (
                        <a className="link" href={`/company/${partnerSlug}/`}>
                          {shortName(c.counterparty)}
                        </a>
                      ) : (
                        shortName(c.counterparty)
                      )
                    ) : (
                      <span className="tag-unknown">상대 미기재</span>
                    )}
                  </td>
                  <td className="num">{period.length > 0 ? period.join(" ~ ") : "-"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {limit < rows.length ? (
        <p style={{ marginTop: "var(--gap-3)" }}>
          <button type="button" className="btn ghost" onClick={() => setLimit(rows.length)}>
            나머지 {rows.length - limit}건 더 보기
          </button>
        </p>
      ) : null}
    </>
  );
}
