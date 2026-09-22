"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

function compress(src,maxSide=1100,quality=.72){
  return new Promise((resolve,reject)=>{
    const img=new Image();
    img.onload=()=>{
      const scale=Math.min(1,maxSide/Math.max(img.width,img.height));
      const c=document.createElement("canvas");
      c.width=Math.max(1,Math.round(img.width*scale));
      c.height=Math.max(1,Math.round(img.height*scale));
      c.getContext("2d").drawImage(img,0,0,c.width,c.height);
      resolve(c.toDataURL("image/jpeg",quality));
    };
    img.onerror=()=>reject(new Error("Unable to read image."));
    img.src=src;
  });
}
function readFile(file,setter){
  if(!file) return;
  if(!file.type.startsWith("image/")){ alert("Please select an image file."); return; }
  const r=new FileReader();
  r.onload=async()=>{ try{ setter(await compress(String(r.result||""))); }catch{ alert("Unable to process image."); } };
  r.readAsDataURL(file);
}

export default function CustomerUpload(){
  const {token}=useParams();
  const [state,setState]=useState({loading:true,status:"",expires_at:null,name:""});
  const [front,setFront]=useState("");
  const [back,setBack]=useState("");
  const [selfie,setSelfie]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");

  useEffect(()=>{
    fetch(`/api/upload-links/${token}`,{cache:"no-store"})
      .then(async r=>({ok:r.ok,data:await r.json()}))
      .then(({ok,data})=>setState({loading:false,status:data.status||(!ok?"INVALID":"WAITING"),expires_at:data.expires_at||null,name:data.name||""}))
      .catch(()=>setState({loading:false,status:"INVALID"}));
  },[token]);

  async function submit(){
    if(!front||!back||!selfie){ setError("Please upload all three images."); return; }
    setBusy(true); setError("");
    try{
      const r=await fetch(`/api/upload-links/${token}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id_front_data_url:front,id_back_data_url:back,selfie_data_url:selfie})});
      const data=await r.json();
      if(!r.ok) throw new Error(data.error||"Upload failed.");
      setState(v=>({...v,status:"COMPLETED"}));
    }catch(e){ setError(e.message||"Upload failed."); }
    finally{ setBusy(false); }
  }

  if(state.loading) return <main className="page"><div className="shell card">Checking link...</div></main>;
  if(state.status==="EXPIRED") return <main className="page"><div className="shell card" style={{maxWidth:720}}><h1>Link Expired</h1><p className="muted">Please request a new upload link.</p></div></main>;
  if(state.status==="INVALID") return <main className="page"><div className="shell card" style={{maxWidth:720}}><h1>Invalid Link</h1><p className="muted">This upload link is not valid.</p></div></main>;
  if(state.status==="COMPLETED") return <main className="page"><div className="shell card" style={{maxWidth:720,textAlign:"center"}}><h1>Upload Completed</h1><p className="muted">Your files have been submitted successfully. This link can no longer be used.</p></div></main>;

  const item=(title,value,setter)=><div><label>{title}</label><label className="btn btn-soft" style={{display:"inline-block"}}>Choose Image<input type="file" accept="image/*" style={{display:"none"}} onChange={e=>readFile(e.target.files?.[0],setter)}/></label>{value&&<img src={value} alt="Preview" style={{width:"100%",maxHeight:260,objectFit:"contain",display:"block",marginTop:10,borderRadius:12,border:"1px solid #dfe6ef"}}/>}</div>;

  return <main className="page"><div className="shell" style={{maxWidth:820}}><div className="card"><h1>Identity Verification</h1><div style={{marginBottom:18,borderRadius:18,overflow:"hidden",border:"1px solid #dfe6ef"}}><img src="/ekyc-guide.png" alt="e-KYC upload guide" style={{width:"100%",display:"block"}}/></div><p className="muted">Upload the identification card front, identification card back and a clear selfie.</p>{state.expires_at&&<p className="muted">This link expires automatically.</p>}<div className="grid2">{item("Identification Card — Front",front,setFront)}{item("Identification Card — Back",back,setBack)}</div><div className="card" style={{marginTop:16}}>{item("Selfie",selfie,setSelfie)}</div>{error&&<div style={{marginTop:12,color:"#b42318",fontWeight:800}}>{error}</div>}<div className="row" style={{justifyContent:"flex-end",marginTop:18}}><button className="btn btn-primary" disabled={busy||!front||!back||!selfie} onClick={submit}>{busy?"Uploading...":"Submit"}</button></div></div></div></main>;
}
