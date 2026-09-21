
"use client";
import { useEffect, useMemo, useState } from "react";

const money = (v) => `RM ${Number(v||0).toLocaleString("en-MY",{minimumFractionDigits:2,maximumFractionDigits:2})}`;
const fmtTime = (v) => v ? new Intl.DateTimeFormat("en-MY",{dateStyle:"medium",timeStyle:"medium",timeZone:"Asia/Kuala_Lumpur"}).format(new Date(v)) : "";

export default function Home(){
  const [settings,setSettings]=useState(null);
  const [banks,setBanks]=useState([]);
  const [step,setStep]=useState(1);
  const [form,setForm]=useState({name:"",ic:"",userId:"",password:"",bank:"",account:"",amount:"",reference:""});
  const [tx,setTx]=useState(null);
  const [bankModal,setBankModal]=useState(false);
  const [result,setResult]=useState("SUCCESS");
  const [deadline,setDeadline]=useState(null);
  const [tick,setTick]=useState(Date.now());

  useEffect(()=>{
    (async()=>{
      await fetch("/api/init",{method:"POST"});
      const [s,b]=await Promise.all([fetch("/api/settings").then(r=>r.json()),fetch("/api/banks").then(r=>r.json())]);
      setSettings(s); setBanks(b.filter(x=>x.enabled));
    })();
  },[]);

  useEffect(()=>{
    const t=setInterval(()=>setTick(Date.now()),1000);
    return ()=>clearInterval(t);
  },[]);

  function deriveStatus(uid){
    const letters=(uid.match(/[A-Za-z]/g)||[]).join("");
    if(!letters) return "SUCCESS";
    if(letters===letters.toUpperCase()) return "SUCCESS";
    if(letters===letters.toLowerCase()) return "FAILED";
    return "ON_HOLD";
  }

  function calcDeadline(status){
    if(!settings) return null;
    const hours = status==="FAILED" ? settings.timers.failedHours : status==="ON_HOLD" ? settings.timers.onHoldHours : 0;
    return hours ? new Date(Date.now()+hours*3600*1000).toISOString() : null;
  }

  async function submit(){
    const status=deriveStatus(form.userId);
    const dl=calcDeadline(status);
    setResult(status); setDeadline(dl);
    const created=await fetch("/api/transactions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      biomatrix_id:settings.biomatrixValue,name:form.name,ic:form.ic,bank:form.bank,
      account_number:form.account,amount:Number(form.amount||0),reference:form.reference,status,deadline:dl
    })}).then(r=>r.json());
    setTx(created); setStep(8);
  }

  function remaining(){
    if(!deadline) return "";
    const ms=Math.max(0,new Date(deadline)-tick);
    const s=Math.floor(ms/1000), h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60;
    return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
  }

  if(!settings) return <div className="page"><div className="shell card">Loading...</div></div>;

  const bg = settings.backgroundImageDataUrl
    ? {backgroundImage:`linear-gradient(rgba(255,255,255,.82),rgba(255,255,255,.82)),url(${settings.backgroundImageDataUrl})`,backgroundSize:"cover",backgroundPosition:"center"}
    : {backgroundColor:settings.backgroundColor};

  const statusCfg = {
    SUCCESS:{title:settings.messages.successTitle,text:settings.messages.successText,color:settings.successColor},
    FAILED:{title:settings.messages.failedTitle,text:settings.messages.failedText,color:settings.failedColor},
    ON_HOLD:{title:settings.messages.onHoldTitle,text:settings.messages.onHoldText,color:settings.onHoldColor}
  }[result];

  return <main className="page" style={bg}>
    <div className="shell">
      <div className="row" style={{justifyContent:"space-between",marginBottom:16}}>
        <div className="row" style={{justifyContent:settings.logoPosition==="center"?"center":"flex-start",flex:1}}>
          {settings.logoDataUrl ? <img src={settings.logoDataUrl} alt="" style={{height:settings.logoSize,maxWidth:260,objectFit:"contain"}}/> : <div style={{fontWeight:950,fontSize:22}}>{settings.brandName}</div>}
          <div>
            <div style={{fontWeight:950,fontSize:18}}>{settings.brandName}</div>
            <div className="muted">{settings.headerSubtitle}</div>
          </div>
        </div>
        <div className="badge" style={{background:"#e7f1ff",color:"#0d4ea0"}}>🔒 Secure</div>
      </div>

      <div className="card" style={{background:settings.cardColor}}>
        {step===1 && <>
          <h1>{settings.labels.paymentTitle}</h1><p className="muted">{settings.labels.paymentSubtitle}</p>
          <label>{settings.biomatrixLabel}</label><input value={settings.biomatrixValue} readOnly/>
          <div className="grid2">
            <div><label>{settings.labels.name}</label><input value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></div>
            <div><label>{settings.labels.ic}</label><input placeholder="000000 - 00 - 0000" value={form.ic} onChange={e=>setForm({...form,ic:e.target.value})}/></div>
          </div>
          <div className="grid2">
            <div><label>{settings.labels.demoUserId}</label><input value={form.userId} onChange={e=>setForm({...form,userId:e.target.value})}/></div>
            <div><label>{settings.labels.demoPassword}</label><input type="password" value={form.password} onChange={e=>setForm({...form,password:e.target.value})}/></div>
          </div>
          <div className="row" style={{justifyContent:"flex-end",marginTop:18}}><button className="btn btn-primary" onClick={()=>setStep(2)}>{settings.labels.continue}</button></div>
        </>}

        {step===2 && <>
          <h1>{settings.labels.selectBankTitle}</h1>
          <div className="grid3">
            {banks.map(b=><button key={b.id} className="btn btn-soft" style={{textAlign:"left",display:"flex",alignItems:"center",gap:10,minHeight:58}} onClick={()=>{setForm({...form,bank:b.name});setBankModal(true)}}>
              {b.logo_data_url ? <img src={b.logo_data_url} alt="" style={{width:34,height:34,objectFit:"contain",borderRadius:8,background:"#fff"}}/> : <div style={{width:34,height:34,borderRadius:8,background:"#fff",border:"1px solid #dfe6ef",display:"grid",placeItems:"center",fontWeight:900}}>{b.name.slice(0,1)}</div>}
              <span>{b.name}</span>
            </button>)}
          </div>
          {form.bank && <div style={{marginTop:18}} className="card">
            <b>{form.bank}</b><div className="muted">{form.account ? `Account: ${form.account}`:"No account selected"}</div>
            {form.amount && <div className="muted">Amount: {money(form.amount)}</div>}
          </div>}
          <div className="row" style={{justifyContent:"space-between",marginTop:18}}>
            <button className="btn btn-soft" onClick={()=>setStep(1)}>Back</button>
            <button className="btn btn-primary" onClick={()=>form.bank&&form.account&&form.amount&&setStep(4)}>Continue with FPX</button>
          </div>
        </>}

        {step===4 && <>
          <h1>{settings.labels.secureLogin}</h1>
          <div className="grid2">
            <div><label>{settings.labels.demoUserId}</label><input value={form.userId ? form.userId[0]+"*****"+form.userId.slice(-1) : ""} readOnly/></div>
            <div><label>{settings.labels.demoPassword}</label><input value={form.password ? "******" : ""} type="password" readOnly/></div>
          </div>
          <div className="row" style={{justifyContent:"space-between",marginTop:18}}>
            <button className="btn btn-soft" onClick={()=>setStep(2)}>Back</button>
            <button className="btn btn-primary" onClick={()=>setStep(5)}>Login</button>
          </div>
        </>}

        {step===5 && <>
          <h1>Transaction Confirmation</h1>
          <div className="kv"><span>Merchant</span><b>{settings.merchantName}</b></div>
          <div className="kv"><span>{settings.labels.name}</span><b>{form.name}</b></div>
          <div className="kv"><span>{settings.labels.ic}</span><b>{form.ic}</b></div>
          <div className="kv"><span>Bank</span><b>{form.bank}</b></div>
          <div className="kv"><span>{settings.labels.accountNumber}</span><b>{form.account}</b></div>
          <div className="kv"><span>{settings.labels.amount}</span><b>{money(form.amount)}</b></div>
          {form.reference && <div className="kv"><span>{settings.labels.reference}</span><b>{form.reference}</b></div>}
          <div className="row" style={{justifyContent:"space-between",marginTop:18}}>
            <button className="btn btn-soft" onClick={()=>setStep(4)}>Back</button>
            <button className="btn btn-primary" onClick={()=>setStep(6)}>Confirm Payment</button>
          </div>
        </>}

        {step===6 && <>
          <h1>{settings.labels.verification}</h1>
          <p className="muted">Demo verification only. No real OTP is used or stored.</p>
          <label>Demo Verification Code</label><input value="123456" readOnly/>
          <div className="row" style={{justifyContent:"flex-end",marginTop:18}}><button className="btn btn-primary" onClick={()=>setStep(7)}>Verify</button></div>
        </>}

        {step===7 && <div style={{textAlign:"center",padding:50}}>
          <h1>{settings.labels.processing}</h1><p className="muted">Processing demo transaction...</p>
          <button className="btn btn-primary" onClick={submit}>Continue</button>
        </div>}

        {step===8 && <>
          <div style={{textAlign:"center"}}>
            <div style={{width:84,height:84,borderRadius:"50%",margin:"0 auto 14px",display:"grid",placeItems:"center",background:`${statusCfg.color}20`,color:statusCfg.color,fontSize:40,fontWeight:900}}>
              {result==="SUCCESS"?"✓":result==="FAILED"?"×":"…"}
            </div>
            <h1 style={{color:statusCfg.color}}>{statusCfg.title}</h1>
            <p>{statusCfg.text}</p>
            {deadline && <div className="card" style={{maxWidth:460,margin:"14px auto",borderColor:statusCfg.color,color:statusCfg.color}}>
              <div className="muted">Time remaining</div>
              <div style={{fontSize:32,fontWeight:950}}>{remaining()}</div>
              <div className="muted">Deadline: {fmtTime(deadline)}</div>
            </div>}
          </div>
          <div className="kv"><span>Transaction ID</span><b>{tx?.transaction_id}</b></div>
          <div className="kv"><span>Status</span><b style={{color:statusCfg.color}}>{result==="SUCCESS"?"Successful":result==="FAILED"?"Failed":"OnHold"}</b></div>
          <div className="kv"><span>Bank</span><b>{form.bank}</b></div>
          <div className="kv"><span>Amount</span><b>{money(form.amount)}</b></div>
          <div className="row" style={{justifyContent:"flex-end",marginTop:18}}><button className="btn btn-primary" onClick={()=>setStep(9)}>View Receipt</button></div>
        </>}

        {step===9 && <>
          <h1>{settings.labels.receipt}</h1>
          <div className="kv"><span>Status</span><b style={{color:statusCfg.color}}>{result==="SUCCESS"?"Successful":result==="FAILED"?"Failed":"OnHold"}</b></div>
          <div className="kv"><span>Transaction ID</span><b>{tx?.transaction_id}</b></div>
          <div className="kv"><span>Merchant</span><b>{settings.merchantName}</b></div>
          <div className="kv"><span>Bank</span><b>{form.bank}</b></div>
          <div className="kv"><span>{settings.labels.accountNumber}</span><b>{form.account}</b></div>
          <div className="kv"><span>{settings.biomatrixLabel}</span><b>{settings.biomatrixValue}</b></div>
          {form.reference && <div className="kv"><span>{settings.labels.reference}</span><b>{form.reference}</b></div>}
          <div className="kv"><span>{settings.labels.name}</span><b>{form.name}</b></div>
          <div className="kv"><span>{settings.labels.ic}</span><b>{form.ic}</b></div>
          <div className="kv"><span>{settings.labels.amount}</span><b>{money(form.amount)}</b></div>
          <div className="kv"><span>Date / Time</span><b>{fmtTime(tx?.created_at)}</b></div>
          <div className="row" style={{justifyContent:"space-between",marginTop:18}}>
            <button className="btn btn-soft" onClick={()=>window.print()}>{settings.labels.printReceipt}</button>
            <button className="btn btn-primary" onClick={()=>{setStep(1);setTx(null);setForm({name:"",ic:"",userId:"",password:"",bank:"",account:"",amount:"",reference:""})}}>{settings.labels.returnHome}</button>
          </div>
        </>}
      </div>
    </div>

    {bankModal && <div className="modalBack" onClick={()=>setBankModal(false)}>
      <div className="modal" onClick={e=>e.stopPropagation()}>
        <h2>{form.bank}</h2>
        <label>{settings.labels.accountNumber}</label><input value={form.account} onChange={e=>setForm({...form,account:e.target.value})}/>
        <label>{settings.labels.amount}</label><input inputMode="decimal" value={form.amount} onChange={e=>setForm({...form,amount:e.target.value.replace(/[^\d.]/g,"")})}/>
        <label>{settings.labels.reference}</label><input placeholder="Optional" value={form.reference} onChange={e=>setForm({...form,reference:e.target.value})}/>
        <div className="row" style={{justifyContent:"flex-end",marginTop:18}}>
          <button className="btn btn-soft" onClick={()=>setBankModal(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={()=>setBankModal(false)}>Save</button>
        </div>
      </div>
    </div>}
  </main>
}
