import { compatible, generarCombinaciones, calcularParlay, mejoresParlays } from "./parlay-engine.js";

let pass = 0, fail = 0;
const t = (nombre, cond) => { if (cond) { pass++; console.log("OK  " + nombre); } else { fail++; console.log("FALLA " + nombre); } };

const picks = [
  { _id: "1", partido: "America vs Chivas", mercado: "Over 2.5", momio: 1.85, probEstimada: 0.62, verdicto: "MANDAR" },
  { _id: "2", partido: "America vs Chivas", mercado: "Under 2.5", momio: 2.0, probEstimada: 0.55, verdicto: "MANDAR" },
  { _id: "3", partido: "Tigres vs Rayados", mercado: "1X2: Local", momio: 1.75, probEstimada: 0.65, verdicto: "MANDAR" },
  { _id: "4", partido: "Pumas vs Toluca", mercado: "Ambos anotan", momio: 1.80, probEstimada: 0.60, verdicto: "MANDAR" },
  { _id: "5", partido: "Cruz Azul vs Leon", mercado: "Over 1.5", momio: 1.70, probEstimada: 0.70, verdicto: "RESERVAS" }
];

// Coherencia
t("Over/Under mismo partido NO compatible", compatible(picks[0], picks[1]) === false);
t("Partidos distintos SI compatibles", compatible(picks[0], picks[2]) === true);
t("Misma pata NO compatible", compatible(picks[0], picks[0]) === false);

// Combinatoria excluye incoherentes
const combos2 = generarCombinaciones(picks, 2);
const tieneIncoherente = combos2.some(c => c[0].partido === c[1].partido);
t("Ninguna combinacion mezcla mismo partido", !tieneIncoherente);

// Calculo
const m = calcularParlay([picks[2], picks[3]]);
t("Momio combinado = producto", Math.abs(m.momioComb - 1.75 * 1.80) < 0.01);
t("Prob conjunta = producto", Math.abs(m.probConjunta - 0.65 * 0.60) < 0.001);

// mejoresParlays solo usa MANDAR
const mejores = mejoresParlays(picks, { minK: 2, maxK: 3, top: 5 });
const usaReservas = mejores.some(pl => pl.patas.some(p => p.verdicto === "RESERVAS"));
t("Parlays NO incluyen picks RESERVAS", !usaReservas);
t("Devuelve al menos 1 parlay valido", mejores.length >= 1);

// Orden por probabilidad conjunta descendente
let ordenado = true;
for (let i = 1; i < mejores.length; i++)
  if (mejores[i].probConjunta > mejores[i-1].probConjunta + 0.0001) ordenado = false;
t("Parlays ordenados por seguridad (prob conjunta desc)", ordenado);

console.log(`\n${pass} pasaron, ${fail} fallaron`);
process.exit(fail ? 1 : 0);
