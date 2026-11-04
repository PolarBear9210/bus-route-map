// 전국 행정동 경계 GeoJSON에서 서울(sido=11)만 걸러 저장한다.
// 사용법: node scripts/extract_seoul.mjs <입력 geojson> <출력 geojson>
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  console.error("사용법: node scripts/extract_seoul.mjs <입력> <출력>");
  process.exit(1);
}

const src = JSON.parse(readFileSync(input, "utf8"));
const features = src.features.filter((f) => f.properties.sido === "11");

mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, JSON.stringify({ type: "FeatureCollection", name: src.name + "_seoul", features }));

const gu = new Set(features.map((f) => f.properties.sggnm));
console.log(`행정동 ${features.length}개, 구 ${gu.size}개 → ${output}`);
