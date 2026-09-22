
"use client";
import { useEffect, useMemo, useState } from "react";

function FileToData({onData,label}) {
  return <label className="btn btn-soft" style={{display:"inline-block"}}>
    {label}
    <input type="file" accept="image/*" style={{display:"none"}} onChange={e=>{
      const f=e.target.files?.[0]; if(!f) return;
      const r=new FileReader(); r.onload=()=>onData(r.result); r.readAsDataURL(f);
    }}/>
  </label>
}

export default function Admin(){
  const [authed,setAuthed]=useState(false);
  const [password,setPassword]=useState("");
  const [settings,setSettings]=useState(null);
  const [banks,setBanks]=useState([]);
  const [txs,setTxs]=useState([]);
  const [salespersons,setSalespersons]=useState([]);
  const [newStaff,setNewStaff]=useState({username:"",salesperson_name:"",password:"",company_name:"",logo_data_url:"",biomatrix_id:"",logo_size:140,bank_name:"",bank_account:"",address:"",enabled:true});
  const [search,setSearch]=useState("");
  const [tab,setTab]=useState("dashboard");
  const [selected,setSelected]=useState(null);
  const [savingStaffId,setSavingStaffId]=useState(null);
  const [uploadPageSize,setUploadPageSize]=useState(10);
  const [uploadPage,setUploadPage]=useState(1);
  const [uploadDate,setUploadDate]=useState("");
  const [uploadSearch,setUploadSearch]=useState("");

  async function loadAll(){
    await fetch("/api/init",{method:"POST"});
    const [s,b,t,sp]=await Promise.all([
      fetch("/api/settings").then(r=>r.json()),
      fetch("/api/banks").then(r=>r.json()),
      fetch("/api/transactions").then(r=>r.json()),
      fetch("/api/salespersons").then(r=>r.json())
    ]);
    setSettings(s);setBanks(b);setTxs(t);setSalespersons(sp);
  }
  useEffect(()=>{ if(authed) loadAll(); },[authed]);

  function login(){
    // Demo-only local admin gate. For real deployment, change to server-side auth.
    if(password === (process.env.NEXT_PUBLIC_ADMIN_PASSWORD || "admin123")) setAuthed(true);
    else if(password==="admin123") setAuthed(true);
    else alert("Use demo admin password: admin123");
  }

  async function saveSettings(){
    await fetch("/api/settings",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(settings)});
    alert("Saved. Frontend will use the new settings.");
  }
  async function saveBanks(){
    await fetch("/api/banks",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(banks)});
    alert("Bank settings saved.");
  }

  async function createSalesperson(){
    const r=await fetch("/api/salespersons",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(newStaff)
    });
    const data=await r.json();
    if(!r.ok){ alert(data.error || "Unable to create salesperson."); return; }
    setSalespersons(x=>[data,...x]);
    setNewStaff({username:"",salesperson_name:"",password:"",company_name:"",logo_data_url:"",biomatrix_id:"",logo_size:140,bank_name:"",bank_account:"",address:"",enabled:true});
  }

  async function saveSalesperson(sp){
    setSavingStaffId(sp.id);
    try{
      const r=await fetch("/api/salespersons",{
        method:"PUT",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify(sp)
      });
      const raw=await r.text();
      let data={};
      try{ data=raw ? JSON.parse(raw) : {}; }catch{ data={error:raw || "Unexpected server response."}; }
      if(!r.ok){
        alert(data.error || `Unable to save salesperson (HTTP ${r.status}).`);
        return;
      }
      setSalespersons(x=>x.map(v=>v.id===data.id?{...v,...data,password:""}:v));
      alert("Salesperson saved successfully.");
    }catch(e){
      alert(`Save failed: ${e?.message || "Network error"}`);
    }finally{
      setSavingStaffId(null);
    }
  }

  async function deleteSalesperson(id){
    if(!confirm("Delete this salesperson?")) return;
    await fetch(`/api/salespersons?id=${id}`,{method:"DELETE"});
    setSalespersons(x=>x.filter(v=>v.id!==id));
  }
  async function updateTx(t,status){
    const hours=status==="FAILED"?settings.timers.failedHours:status==="ON_HOLD"?settings.timers.onHoldHours:0;
    const deadline=hours?new Date(Date.now()+hours*3600*1000).toISOString():null;
    const updated=await fetch(`/api/transactions/${t.id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({status,deadline})}).then(r=>r.json());
    setTxs(x=>x.map(v=>v.id===t.id?updated:v));setSelected(updated);
  }

  function uploadStatus(t){
    if(t.id_front_data_url && t.id_back_data_url && t.selfie_data_url) return "COMPLETED";
    return "WAITING";
  }

  function staffForTransaction(t){
    return salespersons.find(s=>String(s.id)===String(t.salesperson_id))
      || salespersons.find(s=>String(s.username||"")===String(t.salesperson_username||""))
      || null;
  }

  async function saveUploadReview(t, reviewStatus, remark){
    const r=await fetch(`/api/transactions/${t.id}`,{
      method:"PUT",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        upload_review_status:reviewStatus || "",
        upload_remark:remark || ""
      })
    });
    const updated=await r.json();
    if(!r.ok){ alert(updated?.error || "Unable to save."); return; }
    setTxs(x=>x.map(v=>v.id===t.id?updated:v));
    if(selected?.id===t.id) setSelected(updated);
  }

  async function deleteUploadCase(t){
    if(!confirm(`Delete case ${t.transaction_id}?`)) return;
    const r=await fetch(`/api/transactions/${t.id}`,{method:"DELETE"});
    const data=await r.json().catch(()=>({}));
    if(!r.ok){ alert(data?.error || "Unable to delete case."); return; }
    setTxs(x=>x.filter(v=>v.id!==t.id));
    if(selected?.id===t.id) setSelected(null);
  }

  async function openUploadVerification(t){
    try{
      const r=await fetch(`/api/transactions/${t.id}`,{cache:"no-store"});
      const latest=await r.json();
      if(!r.ok || !latest) throw new Error(latest?.error || "Unable to load case.");
      setTxs(x=>x.map(v=>v.id===t.id?latest:v));
      setSelected(latest);
    }catch(e){
      alert(e.message || "Unable to load case.");
    }
  }

  const filtered=useMemo(()=>txs.filter(t=>{
    const q=search.toLowerCase();
    return !q || [t.transaction_id,t.name,t.ic,t.customer_bank_name,t.customer_bank_account,t.customer_address,t.bank,t.account_number,t.status,t.salesperson_username,t.salesperson_company].some(v=>String(v||"").toLowerCase().includes(q));
  }),[txs,search]);

  const uploadFiltered=useMemo(()=>{
    const q=uploadSearch.trim().toLowerCase();

    return [...txs]
      .filter(t=>{
        const staff=staffForTransaction(t);

        const searchableValues=[
          t.transaction_id,
          t.name,
          t.ic,
          t.customer_bank_name,
          t.customer_bank_account,
          t.customer_address,
          t.salesperson_username,
          t.salesperson_company,
          t.biomatrix_id,
          t.upload_review_status,
          t.upload_remark,

          staff?.username,
          staff?.salesperson_name,
          staff?.company_name,
          staff?.biomatrix_id
        ];

        const matchesSearch=!q || searchableValues.some(
          v=>String(v||"").toLowerCase().includes(q)
        );

        const d=t.created_at ? new Date(t.created_at) : null;
        const localDate=d && !Number.isNaN(d.getTime())
          ? `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`
          : "";

        return matchesSearch && (!uploadDate || localDate===uploadDate);
      })
      .sort((a,b)=>new Date(b.created_at||0)-new Date(a.created_at||0));
  },[txs,salespersons,uploadSearch,uploadDate]);

  const uploadTotalPages=Math.max(1,Math.ceil(uploadFiltered.length/uploadPageSize));
  const safeUploadPage=Math.min(uploadPage,uploadTotalPages);
  const uploadPageRows=uploadFiltered.slice((safeUploadPage-1)*uploadPageSize,safeUploadPage*uploadPageSize);

  function formatUploadDateTime(value){
    if(!value) return {date:"-",time:"-"};
    const d=new Date(value);
    if(Number.isNaN(d.getTime())) return {date:"-",time:"-"};
    return {
      date:d.toLocaleDateString("en-GB"),
      time:d.toLocaleTimeString("en-GB",{hour:"2-digit",minute:"2-digit",second:"2-digit"})
    };
  }

  if(!authed) return <main className="page" style={{background:"#eef1eb"}}>
    <div className="shell" style={{maxWidth:460}}>
      <div className="card">
        <h1>Admin</h1><p className="muted">All-in-One Demo Control</p>
        <label>Password</label><input type="password" value={password} onChange={e=>setPassword(e.target.value)} onKeyDown={e=>e.key==="Enter"&&login()}/>
        <button className="btn btn-primary" style={{width:"100%",marginTop:16}} onClick={login}>Login</button>
        <div className="muted" style={{marginTop:10}}>Demo password: admin123</div>
      </div>
    </div>
  </main>;

  if(!settings) return <main className="page"><div className="shell card">Loading...</div></main>;

  const stat = {
    total:txs.length,
    success:txs.filter(x=>x.status==="SUCCESS").length,
    failed:txs.filter(x=>x.status==="FAILED").length,
    onhold:txs.filter(x=>x.status==="ON_HOLD").length
  };

  const set=(path,val)=>{
    const parts=path.split(".");
    const copy=structuredClone(settings);
    let p=copy;
    for(let i=0;i<parts.length-1;i++) p=p[parts[i]];
    p[parts.at(-1)]=val;
    setSettings(copy);
  };

  return <main className="page" style={{background:"#f4f6f9"}}>
    <div className="shell">
      <div className="row" style={{justifyContent:"space-between",marginBottom:14}}>
        <div><h1 style={{margin:0}}>All-in-One Admin</h1><div className="muted">Controls the frontend demo</div></div>
        <div className="row">
          {["dashboard","salespersons","branding","text","banks","uploads","transactions"].map(x=><button key={x} className="btn btn-soft" onClick={()=>setTab(x)}>{x}</button>)}
          <button className="btn btn-soft" onClick={()=>setAuthed(false)}>Logout</button>
        </div>
      </div>

      {tab==="dashboard" && <>
        <div className="grid3">
          <div className="card"><div className="muted">Transactions</div><div style={{fontSize:34,fontWeight:950}}>{stat.total}</div></div>
          <div className="card"><div className="muted">Successful</div><div style={{fontSize:34,fontWeight:950,color:settings.successColor}}>{stat.success}</div></div>
          <div className="card"><div className="muted">Failed / OnHold</div><div style={{fontSize:34,fontWeight:950}}>{stat.failed} / {stat.onhold}</div></div>
        </div>
        <div className="card" style={{marginTop:14}}>
          <h2>Quick settings</h2>
          <div className="grid2">
            <div><label>BioMatrix ID</label><input value={settings.biomatrixValue} onChange={e=>set("biomatrixValue",e.target.value)}/></div>
            <div><label>Merchant</label><input value={settings.merchantName} onChange={e=>set("merchantName",e.target.value)}/></div>
          </div>
          <button className="btn btn-primary" style={{marginTop:14}} onClick={saveSettings}>Save</button>
        </div>
      </>}


      {tab==="salespersons" && <div className="card">
        <h2>Salespersons / Staff Login</h2>
        <p className="muted">Each salesperson has a separate User ID, password, company name and logo. Adjust Logo Size with the slider and preview it here before saving.</p>

        <div className="card" style={{marginBottom:18,background:"#f8fafc"}}>
          <h3 style={{marginTop:0}}>Add Salesperson</h3>
          <div className="grid2">
            <div><label>User ID</label><input value={newStaff.username} onChange={e=>setNewStaff({...newStaff,username:e.target.value})}/></div>
            <div><label>Salesperson Name</label><input value={newStaff.salesperson_name || ""} onChange={e=>setNewStaff({...newStaff,salesperson_name:e.target.value})}/></div>
            <div><label>Password</label><input type="password" value={newStaff.password} onChange={e=>setNewStaff({...newStaff,password:e.target.value})}/></div>
            <div><label>Company Name</label><input value={newStaff.company_name} onChange={e=>setNewStaff({...newStaff,company_name:e.target.value})}/></div>
            <div><label>BioMatrix ID (optional)</label><input placeholder="Leave blank = auto-generate" value={newStaff.biomatrix_id || ""} onChange={e=>setNewStaff({...newStaff,biomatrix_id:e.target.value})}/></div>
            <div>
              <label>Company Logo</label>
              <FileToData label="Upload Logo" onData={d=>setNewStaff({...newStaff,logo_data_url:d})}/>
            </div>
            <div>
              <label>Logo Size: {newStaff.logo_size || 140}px</label>
              <input type="range" min="60" max="260" step="5" value={newStaff.logo_size || 140} onChange={e=>setNewStaff({...newStaff,logo_size:Number(e.target.value)})}/>
              <input type="number" min="60" max="260" value={newStaff.logo_size || 140} onChange={e=>setNewStaff({...newStaff,logo_size:Number(e.target.value)})} style={{marginTop:8}}/>
            </div>
            {newStaff.logo_data_url && <div style={{gridColumn:"1 / -1"}}>
              <label>Live Preview</label>
              <div style={{minHeight:120,padding:16,border:"1px dashed #cbd6e3",borderRadius:14,background:"#fff",display:"flex",alignItems:"center"}}>
                <img src={newStaff.logo_data_url} alt="" style={{height:Math.max(60,Math.min(260,Number(newStaff.logo_size || 140))),maxWidth:"100%",objectFit:"contain"}}/>
              </div>
            </div>}
          </div>
          <button className="btn btn-primary" style={{marginTop:14}} onClick={createSalesperson}>Add Salesperson</button>
        </div>

        {salespersons.length===0 && <div className="muted">No salesperson accounts yet.</div>}

        {salespersons.map(sp=><div key={sp.id} className="card" style={{marginBottom:14}}>
          <div className="row" style={{alignItems:"flex-start"}}>
            <div style={{width:Math.max(150,Math.min(360,Number(sp.logo_size || 140)*2)),minHeight:120,border:"1px dashed #cbd6e3",borderRadius:14,display:"grid",placeItems:"center",overflow:"hidden",background:"#fff",padding:10}}>
              {sp.logo_data_url ? <img src={sp.logo_data_url} alt="" style={{height:Math.max(60,Math.min(260,Number(sp.logo_size || 140))),maxWidth:"100%",objectFit:"contain"}}/> : <b>{sp.company_name?.slice(0,1) || "S"}</b>}
            </div>
            <div style={{flex:1}}>
              <div className="grid2">
                <div><label>User ID</label><input value={sp.username} onChange={e=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,username:e.target.value}:v))}/></div>
                <div><label>Salesperson Name</label><input value={sp.salesperson_name || ""} onChange={e=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,salesperson_name:e.target.value}:v))}/></div>
                <div><label>Company Name</label><input value={sp.company_name} onChange={e=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,company_name:e.target.value}:v))}/></div>
                <div><label>New Password (leave blank to keep)</label><input type="password" value={sp.password || ""} onChange={e=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,password:e.target.value}:v))}/></div>
                <div><label>BioMatrix ID</label><input value={sp.biomatrix_id || ""} onChange={e=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,biomatrix_id:e.target.value}:v))}/></div>
                <div>
                  <label>Logo Size: {sp.logo_size || 140}px</label>
                  <input type="range" min="60" max="260" step="5" value={sp.logo_size || 140} onChange={e=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,logo_size:Number(e.target.value)}:v))}/>
                  <input type="number" min="60" max="260" value={sp.logo_size || 140} onChange={e=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,logo_size:Number(e.target.value)}:v))} style={{marginTop:8}}/>
                </div>
                <div>
                  <label>Status</label>
                  <select value={sp.enabled?"enabled":"disabled"} onChange={e=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,enabled:e.target.value==="enabled"}:v))}>
                    <option value="enabled">Enabled</option>
                    <option value="disabled">Disabled</option>
                  </select>
                </div>
              </div>
              <div className="row" style={{marginTop:12}}>
                <FileToData label="Replace Logo" onData={d=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,logo_data_url:d}:v))}/>
                <button className="btn btn-soft" onClick={()=>setSalespersons(x=>x.map(v=>v.id===sp.id?{...v,logo_data_url:""}:v))}>Remove Logo</button>
                <button className="btn btn-primary" disabled={savingStaffId===sp.id} onClick={()=>saveSalesperson(sp)}>{savingStaffId===sp.id?"Saving...":"Save"}</button>
                <button className="btn btn-soft" onClick={()=>deleteSalesperson(sp.id)}>Delete</button>
              </div>
            </div>
          </div>
        </div>)}
      </div>}

      {tab==="branding" && <div className="card">
        <h2>Branding / Appearance</h2>
        <div className="grid2">
          <div><label>Brand name</label><input value={settings.brandName} onChange={e=>set("brandName",e.target.value)}/></div>
          <div><label>Header subtitle</label><input value={settings.headerSubtitle} onChange={e=>set("headerSubtitle",e.target.value)}/></div>
          <div><label>Merchant name</label><input value={settings.merchantName} onChange={e=>set("merchantName",e.target.value)}/></div>
          <div><label>Logo size</label><input type="number" value={settings.logoSize} onChange={e=>set("logoSize",Number(e.target.value))}/></div>
          <div><label>Logo position</label><select value={settings.logoPosition} onChange={e=>set("logoPosition",e.target.value)}><option>left</option><option>center</option><option>right</option></select></div>
          <div><label>Background color</label><input type="color" value={settings.backgroundColor} onChange={e=>set("backgroundColor",e.target.value)}/></div>
          <div><label>Primary color</label><input type="color" value={settings.primaryColor} onChange={e=>set("primaryColor",e.target.value)}/></div>
          <div><label>Success color</label><input type="color" value={settings.successColor} onChange={e=>set("successColor",e.target.value)}/></div>
          <div><label>Failed color</label><input type="color" value={settings.failedColor} onChange={e=>set("failedColor",e.target.value)}/></div>
          <div><label>OnHold color</label><input type="color" value={settings.onHoldColor} onChange={e=>set("onHoldColor",e.target.value)}/></div>
        </div>
        <div className="row" style={{marginTop:14}}>
          <FileToData label="Upload Logo" onData={d=>set("logoDataUrl",d)}/>
          <button className="btn btn-soft" onClick={()=>set("logoDataUrl","")}>Remove Logo</button>
          <FileToData label="Upload Background" onData={d=>set("backgroundImageDataUrl",d)}/>
          <button className="btn btn-soft" onClick={()=>set("backgroundImageDataUrl","")}>Remove Background</button>
        </div>
        {settings.logoDataUrl && <img src={settings.logoDataUrl} alt="" style={{maxHeight:100,maxWidth:320,marginTop:14}}/>}
        <button className="btn btn-primary" style={{marginTop:18}} onClick={saveSettings}>Save Branding</button>
      </div>}

      {tab==="text" && <div className="card">
        <h2>All Text / Timers</h2>
        <div className="grid2">
          {Object.entries(settings.labels).map(([k,v])=><div key={k}><label>{k}</label><input value={v} onChange={e=>set(`labels.${k}`,e.target.value)}/></div>)}
        </div>
        <h3>Messages</h3>
        {Object.entries(settings.messages).map(([k,v])=><div key={k}><label>{k}</label><textarea rows={3} value={v} onChange={e=>set(`messages.${k}`,e.target.value)}/></div>)}
        <div className="grid2">
          <div><label>Failed timer hours</label><input type="number" value={settings.timers.failedHours} onChange={e=>set("timers.failedHours",Number(e.target.value))}/></div>
          <div><label>OnHold timer hours</label><input type="number" value={settings.timers.onHoldHours} onChange={e=>set("timers.onHoldHours",Number(e.target.value))}/></div>
        </div>
        <button className="btn btn-primary" style={{marginTop:18}} onClick={saveSettings}>Save Text / Timers</button>
      </div>}

      {tab==="banks" && <div className="card">
        <h2>Banks</h2><p className="muted">Enable / disable banks, upload each bank logo, and reorder them. Click Save Banks after changes.</p>
        {banks.map((b,i)=><div className="row" key={b.id} style={{padding:"10px 0",borderBottom:"1px solid #edf1f5"}}>
          <input type="checkbox" style={{width:18}} checked={b.enabled} onChange={e=>setBanks(x=>x.map(v=>v.id===b.id?{...v,enabled:e.target.checked}:v))}/>
          <div style={{width:44,height:44,border:"1px solid #dfe6ef",borderRadius:10,background:"#fff",display:"grid",placeItems:"center",overflow:"hidden"}}>
            {b.logo_data_url ? <img src={b.logo_data_url} alt="" style={{width:"100%",height:"100%",objectFit:"contain"}}/> : <span style={{fontWeight:900}}>{b.name.slice(0,1)}</span>}
          </div>
          <div style={{flex:1,minWidth:220}}>{b.name}</div>
          <label className="btn btn-soft" style={{display:"inline-block"}}>
            Upload Logo
            <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" style={{display:"none"}} onChange={e=>{
              const f=e.target.files?.[0]; if(!f) return;
              const r=new FileReader();
              r.onload=()=>setBanks(x=>x.map(v=>v.id===b.id?{...v,logo_data_url:r.result}:v));
              r.readAsDataURL(f);
            }}/>
          </label>
          {b.logo_data_url && <button className="btn btn-soft" onClick={()=>setBanks(x=>x.map(v=>v.id===b.id?{...v,logo_data_url:""}:v))}>Remove Logo</button>}
          <button className="btn btn-soft" onClick={()=>i>0&&setBanks(x=>{const a=[...x];[a[i-1],a[i]]=[a[i],a[i-1]];return a})}>↑</button>
          <button className="btn btn-soft" onClick={()=>i<banks.length-1&&setBanks(x=>{const a=[...x];[a[i+1],a[i]]=[a[i],a[i+1]];return a})}>↓</button>
        </div>)}
        <button className="btn btn-primary" style={{marginTop:18}} onClick={saveBanks}>Save Banks</button>
      </div>}

      {tab==="uploads" && <div className="card">
        <div className="row" style={{justifyContent:"space-between",alignItems:"center",gap:12,flexWrap:"wrap"}}>
          <div>
            <h2 style={{marginBottom:4}}>Upload Verification</h2>
            <div className="muted">Check the customer's ID front, ID back and selfie.</div>
          </div>
          <button className="btn btn-soft" onClick={loadAll}>Refresh</button>
        </div>

        <div className="row" style={{marginTop:16,gap:10,flexWrap:"wrap",alignItems:"end"}}>
          <div style={{minWidth:240,flex:"1 1 260px"}}>
            <label>Search</label>
            <input
              placeholder="Case / staff / customer / IC / bank..."
              value={uploadSearch}
              onChange={e=>{setUploadSearch(e.target.value);setUploadPage(1);}}
            />
          </div>

          <div style={{minWidth:170}}>
            <label>Date</label>
            <input
              type="date"
              value={uploadDate}
              onChange={e=>{setUploadDate(e.target.value);setUploadPage(1);}}
            />
          </div>

          <div style={{minWidth:130}}>
            <label>Show</label>
            <select
              value={uploadPageSize}
              onChange={e=>{setUploadPageSize(Number(e.target.value));setUploadPage(1);}}
            >
              <option value={10}>10</option>
              <option value={30}>30</option>
              <option value={60}>60</option>
              <option value={100}>100</option>
            </select>
          </div>

          {(uploadDate || uploadSearch) && (
            <button className="btn btn-soft" onClick={()=>{setUploadDate("");setUploadSearch("");setUploadPage(1);}}>
              Clear
            </button>
          )}
        </div>

        <div className="muted" style={{marginTop:10}}>
          Showing {uploadFiltered.length===0?0:(safeUploadPage-1)*uploadPageSize+1}
          {" - "}
          {Math.min(safeUploadPage*uploadPageSize,uploadFiltered.length)}
          {" of "}
          {uploadFiltered.length}
        </div>

        <div style={{marginTop:12,display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(290px,1fr))",gap:12}}>
          {uploadPageRows.map(t=>{
            const staff=staffForTransaction(t);
            const dt=formatUploadDateTime(t.created_at);
            return <div key={t.id} style={{border:"1px solid #e1e7ef",borderRadius:16,padding:14,background:"#fff",minWidth:0}}>
              <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start",flexWrap:"wrap"}}>
                <div style={{minWidth:0}}>
                  <div className="muted" style={{fontSize:11}}>CASE</div>
                  <div style={{fontWeight:900,wordBreak:"break-all"}}>{t.transaction_id}</div>
                </div>
                <div style={{fontSize:12,textAlign:"right"}}>
                  <div>{dt.date}</div>
                  <div className="muted">{dt.time}</div>
                </div>
              </div>

              <div style={{marginTop:12,display:"grid",gridTemplateColumns:"1fr 1fr",gap:10}}>
                <div>
                  <div className="muted" style={{fontSize:11}}>STAFF</div>
                  <div style={{fontWeight:850,wordBreak:"break-word"}}>{staff?.salesperson_name||t.salesperson_username||"-"}</div>
                  <div className="muted" style={{fontSize:12}}>User ID: {t.salesperson_username||staff?.username||"-"}</div>
                  <div className="muted" style={{fontSize:12,wordBreak:"break-word"}}>{t.salesperson_company||staff?.company_name||"-"}</div>
                  <div className="muted" style={{fontSize:12,wordBreak:"break-word"}}>{t.biomatrix_id||staff?.biomatrix_id||"-"}</div>
                </div>

                <div>
                  <div className="muted" style={{fontSize:11}}>CUSTOMER</div>
                  <div style={{fontWeight:850,wordBreak:"break-word"}}>{t.name||"-"}</div>
                  <div style={{marginTop:6}}><b>{uploadStatus(t)}</b></div>
                </div>
              </div>

              <div style={{marginTop:12,padding:10,borderRadius:12,background:"#f8fafc"}}>
                <div className="muted" style={{fontSize:11,marginBottom:6}}>UPLOADS</div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:8,textAlign:"center"}}>
                  <div>
                    <div style={{fontSize:12,fontWeight:800}}>Front</div>
                    <div style={{fontSize:18}}>{t.id_front_data_url?"✅":"—"}</div>
                  </div>
                  <div>
                    <div style={{fontSize:12,fontWeight:800}}>Back</div>
                    <div style={{fontSize:18}}>{t.id_back_data_url?"✅":"—"}</div>
                  </div>
                  <div>
                    <div style={{fontSize:12,fontWeight:800}}>Selfie</div>
                    <div style={{fontSize:18}}>{t.selfie_data_url?"✅":"—"}</div>
                  </div>
                </div>
              </div>

              <div style={{marginTop:12}}>
                <label>Review Status</label>
                <select
                  style={{width:"100%"}}
                  value={t.upload_review_status||""}
                  onChange={e=>setTxs(x=>x.map(v=>v.id===t.id?{...v,upload_review_status:e.target.value}:v))}
                >
                  <option value="">Select</option>
                  <option value="WAITING">Waiting</option>
                  <option value="CONTACTED">Contacted</option>
                  <option value="CHECKING">Checking</option>
                  <option value="NEED_REUPLOAD">Need Re-upload</option>
                  <option value="COMPLETED">Completed</option>
                  <option value="CANCELLED">Cancelled</option>
                </select>
              </div>

              <div style={{marginTop:10}}>
                <label>Remark (optional)</label>
                <input
                  style={{width:"100%"}}
                  placeholder="Optional..."
                  value={t.upload_remark||""}
                  onChange={e=>setTxs(x=>x.map(v=>v.id===t.id?{...v,upload_remark:e.target.value}:v))}
                />
              </div>

              <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:8,marginTop:12}}>
                <button
                  className="btn btn-soft"
                  style={{padding:"9px 8px"}}
                  onClick={()=>{
                    const cur=txs.find(v=>v.id===t.id) || t;
                    saveUploadReview(t,cur.upload_review_status||"",cur.upload_remark||"");
                  }}
                >
                  Save
                </button>
                <button className="btn btn-primary" style={{padding:"9px 8px"}} onClick={()=>openUploadVerification(t)}>Verify</button>
                <button className="btn btn-soft" style={{padding:"9px 8px"}} onClick={()=>deleteUploadCase(t)}>Delete</button>
              </div>
            </div>
          })}
        </div>

        <div className="row" style={{justifyContent:"space-between",alignItems:"center",marginTop:16,flexWrap:"wrap"}}>
          <div className="muted">Page {safeUploadPage} / {uploadTotalPages}</div>
          <div className="row">
            <button
              className="btn btn-soft"
              disabled={safeUploadPage<=1}
              onClick={()=>setUploadPage(p=>Math.max(1,p-1))}
            >
              Previous
            </button>
            <button
              className="btn btn-soft"
              disabled={safeUploadPage>=uploadTotalPages}
              onClick={()=>setUploadPage(p=>Math.min(uploadTotalPages,p+1))}
            >
              Next
            </button>
          </div>
        </div>
      </div>}

      {tab==="transactions" && <div className="card">
        <div className="row" style={{justifyContent:"space-between"}}><h2>Transactions</h2><input style={{maxWidth:320}} placeholder="Search..." value={search} onChange={e=>setSearch(e.target.value)}/></div>
        <div style={{overflowX:"auto"}}>
          <table className="table"><thead><tr><th>ID</th><th>Staff</th><th>Company</th><th>Name</th><th>Bank</th><th>Amount</th><th>Status</th><th></th></tr></thead>
          <tbody>{filtered.map(t=><tr key={t.id}>
            <td>{t.transaction_id}</td><td>{t.salesperson_username||"-"}</td><td>{t.salesperson_company||"-"}</td><td>{t.name}</td><td>{t.bank}</td><td>RM {Number(t.amount).toFixed(2)}</td><td>{t.status}</td>
            <td><button className="btn btn-soft" onClick={()=>setSelected(t)}>Open</button></td>
          </tr>)}</tbody></table>
        </div>
      </div>}

      {selected && <div className="modalBack" onClick={()=>setSelected(null)}>
        <div className="modal" onClick={e=>e.stopPropagation()}>
          <h2>{selected.transaction_id}</h2>
          {(()=>{
            const staff=staffForTransaction(selected);
            return <>
              <div className="kv"><span>User ID</span><b>{selected.salesperson_username||staff?.username||"-"}</b></div>
              <div className="kv"><span>Salesperson Name</span><b>{staff?.salesperson_name||"-"}</b></div>
              <div className="kv"><span>Company</span><b>{selected.salesperson_company||staff?.company_name||"-"}</b></div>
              <div className="kv"><span>BioMatrix ID</span><b>{selected.biomatrix_id||staff?.biomatrix_id||"-"}</b></div>
            </>;
          })()}
          {["name","ic","customer_bank_name","customer_bank_account","customer_address","bank","account_number","amount","reference","status","deadline","created_at"].map(k=><div className="kv" key={k}><span>{k}</span><b>{String(selected[k]??"")}</b></div>)}

          <div style={{marginTop:18,padding:14,border:"1px solid #dfe6ef",borderRadius:12}}>
            <div className="kv"><span>Upload Status</span><b>{uploadStatus(selected)}</b></div>
            <div style={{marginTop:10}}>
              <label>Review Status</label>
              <select
                value={selected.upload_review_status||""}
                onChange={e=>setSelected({...selected,upload_review_status:e.target.value})}
              >
                <option value="">Select</option>
                <option value="WAITING">Waiting</option>
                <option value="CONTACTED">Contacted</option>
                <option value="CHECKING">Checking</option>
                <option value="NEED_REUPLOAD">Need Re-upload</option>
                <option value="COMPLETED">Completed</option>
                <option value="CANCELLED">Cancelled</option>
              </select>

              <label style={{marginTop:10}}>Remark (optional)</label>
              <textarea
                rows={3}
                value={selected.upload_remark||""}
                onChange={e=>setSelected({...selected,upload_remark:e.target.value})}
                placeholder="Write anything here only when needed..."
              />

              <div style={{marginTop:10}}>
                <button
                  className="btn btn-soft"
                  onClick={()=>saveUploadReview(
                    selected,
                    selected.upload_review_status||"",
                    selected.upload_remark||""
                  )}
                >
                  Save
                </button>
              </div>
            </div>
          </div>

          <div style={{marginTop:18}}>
            <h3>KYC Images</h3>
            <div className="grid3">
              <div>
                <div className="muted" style={{marginBottom:6}}>ID Front</div>
                {selected.id_front_data_url
                  ? <a href={selected.id_front_data_url} target="_blank" rel="noreferrer"><img src={selected.id_front_data_url} alt="ID front" style={{width:"100%",height:180,objectFit:"contain",border:"1px solid #dfe6ef",borderRadius:12,background:"#fff"}}/></a>
                  : <div className="muted">Not uploaded</div>}
              </div>
              <div>
                <div className="muted" style={{marginBottom:6}}>ID Back</div>
                {selected.id_back_data_url
                  ? <a href={selected.id_back_data_url} target="_blank" rel="noreferrer"><img src={selected.id_back_data_url} alt="ID back" style={{width:"100%",height:180,objectFit:"contain",border:"1px solid #dfe6ef",borderRadius:12,background:"#fff"}}/></a>
                  : <div className="muted">Not uploaded</div>}
              </div>
              <div>
                <div className="muted" style={{marginBottom:6}}>Selfie</div>
                {selected.selfie_data_url
                  ? <a href={selected.selfie_data_url} target="_blank" rel="noreferrer"><img src={selected.selfie_data_url} alt="Selfie" style={{width:"100%",height:180,objectFit:"cover",border:"1px solid #dfe6ef",borderRadius:12,background:"#fff"}}/></a>
                  : <div className="muted">Not uploaded</div>}
              </div>
            </div>
          </div>

          <div className="row" style={{marginTop:16}}>
            <button className="btn" style={{background:settings.successColor,color:"#fff"}} onClick={()=>updateTx(selected,"SUCCESS")}>Successful</button>
            <button className="btn" style={{background:settings.failedColor,color:"#fff"}} onClick={()=>updateTx(selected,"FAILED")}>Failed</button>
            <button className="btn" style={{background:settings.onHoldColor,color:"#fff"}} onClick={()=>updateTx(selected,"ON_HOLD")}>OnHold</button>
            <button className="btn btn-soft" onClick={()=>setSelected(null)}>Close</button>
          </div>
        </div>
      </div>}
    </div>
  </main>
}
