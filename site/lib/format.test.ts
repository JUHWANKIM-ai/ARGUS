/** 금액·이름 표기의 계약. 실행: `npm test` */
import { test } from "node:test";
import assert from "node:assert/strict";
import { nameKey, shortName, slugIndex, won, ym, ymd } from "./format.ts";

test("조 단위 금액은 억까지 남긴다 — 어림하지 않는다", () => {
  assert.equal(won(22764764160000), "22조 7,648억");
  assert.equal(won(1770780000000), "1조 7,708억");
});

test("억 미만은 원으로 쓴다", () => {
  assert.equal(won(428249000000), "4,282억");
  assert.equal(won(5000000), "5,000,000원");
});

test("조 단위가 딱 떨어지면 억을 붙이지 않는다", () => {
  assert.equal(won(3e12), "3조");
});

test("반올림이 1조를 채우면 조로 올린다", () => {
  assert.equal(won(1999999999999), "2조");
});

test("금액이 없으면 null 이다 — 0으로도 어림수로도 쓰지 않는다", () => {
  assert.equal(won(null), null);
  assert.equal(won(undefined), null);
});

test("날짜는 점으로 끊고, 형식이 다르면 손대지 않는다", () => {
  assert.equal(ymd("20250731"), "2025.07.31");
  assert.equal(ymd(null), "-");
  assert.equal(ymd("미상"), "미상");
  assert.equal(ym("2025-07-24"), "2025.07");
});

test("법인격 표기를 뗀다 — 앞이든 뒤든", () => {
  assert.equal(shortName("한국수력원자력(주)"), "한국수력원자력");
  assert.equal(shortName("한국항공우주산업㈜"), "한국항공우주산업");
  assert.equal(shortName("현대자동차 주식회사"), "현대자동차");
  assert.equal(shortName("주식회사 청주테크노폴리스"), "청주테크노폴리스");
});

test("이름 열쇠는 대소문자와 공백을 무시한다", () => {
  assert.equal(nameKey("SK하이닉스"), nameKey("sk 하이닉스"));
});

test("관계망 상대가 우리 70개사면 slug 를 찾는다", () => {
  const index = slugIndex([{ name: "삼성전자", slug: "00126380" }]);
  assert.equal(index[nameKey("삼성전자(주)")], "00126380");
  assert.equal(index[nameKey("방위사업청")], undefined); // 발주처는 우리 회사가 아니다
});
