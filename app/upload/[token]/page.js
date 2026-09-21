"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

function compress(file, maxSide=1100, quality=0.72){
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>{
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
      img.src=String(reader.result||"");
    };
    reader.onerror=()=>reject(new Error("Unable to read file."));
    reader.readAsDataURL(file);
  });
}

export default function CustomerUpload(){
  const { token } = useParams();
  const [state,setState]=useState({loading:true,status:"",name:"",error:""});
  const [front,setFront]=useState("");
  const [back,setBack]=useState("");
  const [selfie,setSelfie]=useState("");
  const [busy,setBusy]=useState(false);

  useEffect(()=>{
    fetch(`/api/upload-links/${token}`,{cache:"no-store"})
      .then(async r=>({ok:r.ok,data:await r.json()}))
      .then(({ok,data})=>setState({loading:false,status:data.status||"",name:data.name||"",error:ok?"":(data.error||"Unable to open link.")}))
      .catch(()=>setState({loading:false,status:"",name:"",error:"Unable to open link."}));
  },[token]);

  async function pick(e,setter){
    const f=e.target.files?.[0];
    if(!f) return;
    if(!f.type.startsWith("image/")) return alert("Please select an image file.");
    try{ setter(await compress(f)); }catch{ alert("Unable to process image."); }
  }

  async function submit(){
    if(!front||!back||!selfie) return alert("Please upload all three images.");
    setBusy(true);
    try{
      const r=await fetch(`/api/upload-links/${token}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({id_front_data_url:front,id_back_data_url:back,selfie_data_url:selfie})});
      const data=await r.json();
      if(!r.ok) throw new Error(data.error||"Upload failed.");
      setState(v=>({...v,status:"Completed",error:""}));
    }catch(e){ setState(v=>({...v,error:e.message||"Upload failed."})); }
    finally{ setBusy(false); }
  }

  if(state.loading) return <main style={{maxWidth:760,margin:"60px auto",padding:20,fontFamily:"Arial"}}>Loading...</main>;
  if(state.error) return <main style={{maxWidth:760,margin:"60px auto",padding:20,fontFamily:"Arial"}}><h2>{state.error}</h2></main>;
  if(state.status==="Completed") return <main style={{maxWidth:760,margin:"60px auto",padding:20,fontFamily:"Arial"}}><h1>Upload Completed</h1><p>Your files have been submitted successfully.</p></main>;

  const box={border:"1px solid #dfe6ef",borderRadius:16,padding:16,background:"#fff"};
  return <main style={{maxWidth:820,margin:"30px auto",padding:20,fontFamily:"Arial",background:"#f5f7fb",minHeight:"100vh"}}>
    <div style={box}>
      <h1>Identity Verification</h1>
      <img src="/ekyc-guide.png" alt="e-KYC guide" style={{width:"100%",borderRadius:14,marginBottom:18}}/>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:14}}>
        {[['Identification Card — Front',front,setFront],['Identification Card — Back',back,setBack],['Selfie',selfie,setSelfie]].map(([label,val,setter])=><div key={label} style={box}>
          <b>{label}</b>
          <input type="file" accept="image/*" onChange={e=>pick(e,setter)} style={{display:"block",marginTop:12}}/>
          {val && <img src={val} alt="preview" style={{width:"100%",maxHeight:220,objectFit:"contain",marginTop:12,borderRadius:10}}/>}
        </div>)}
      </div>
      {state.error && <div style={{color:"#b42318",fontWeight:700,marginTop:14}}>{state.error}</div>}
      <button onClick={submit} disabled={busy||!front||!back||!selfie} style={{marginTop:18,padding:"12px 18px",border:0,borderRadius:10,background:"#1264d8",color:"white",fontWeight:800,cursor:"pointer"}}>{busy?"Uploading...":"Submit"}</button>
    </div>
  </main>;
}
