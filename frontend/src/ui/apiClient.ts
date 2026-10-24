let csrfToken='';
export function setCsrfToken(value: string){csrfToken=value}
export async function api<T>(url:string,body?:unknown,method?:string):Promise<T>{
 const verb=method||(body===undefined?'GET':'POST');
 const headers:Record<string,string>={};
 if(body!==undefined)headers['Content-Type']='application/json';
 if(verb!=='GET'&&csrfToken)headers['X-CSRF-Token']=csrfToken;
 const response=await fetch(url,{method:verb,headers,credentials:'include',...(body===undefined?{}:{body:JSON.stringify(body)})});
 if(!response.ok){const payload=await response.json().catch(()=>null);throw new Error(payload?.detail?.message||`Request failed (${response.status}).`)}
 if(response.status===204)return undefined as T;
 return response.json();
}
