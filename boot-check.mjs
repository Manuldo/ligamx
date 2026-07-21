// Verifica que TODOS los modulos resuelvan sus imports entre si.
// node --check no detecta esto: solo valida sintaxis por archivo.
const mods=["./server.js","./db.js","./parlay-engine.js","./asyncHandler.js",
 "./routes/auth.js","./routes/picks.js","./routes/admin.js","./routes/payments.js",
 "./routes/parlays.js","./routes/matches.js","./routes/history.js","./routes/record.js",
 "./routes/league.js","./middleware/auth.js",
 "./models/User.js","./models/Pick.js","./models/Vote.js","./models/Parlay.js",
 "./models/Match.js","./models/MatchAnalysis.js","./models/UserPick.js",
 "./models/Standing.js","./models/PlayerStat.js",
 "./models/PasswordReset.js","./mailer.js"];
process.env.MONGODB_URI="mongodb://localhost:1/x";
process.env.JWT_SECRET="x";process.env.ADMIN_KEY="x";
let ok=0,bad=0;
for(const m of mods){
  if(m==="./server.js")continue; // arranca listener, se prueba aparte
  try{ await import(m); ok++; }
  catch(e){ bad++; console.log("FALLA "+m+" -> "+e.message); }
}
console.log(`${ok} modulos importan bien, ${bad} fallan`);
process.exit(bad?1:0);
