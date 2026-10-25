let csrfToken='';
export function setCsrfToken(value: string){csrfToken=value}

/**
 * Map raw server error messages to user-friendly, actionable strings.
 * Preserves specific validation messages (dimensions, geometry constraints)
 * while replacing generic HTTP codes and internal details.
 */
export function friendlyError(message: string, status: number): string {
  // Specific actionable messages pass through directly
  if (
    message.includes('diameter') ||
    message.includes('height') ||
    message.includes('width') ||
    message.includes('depth') ||
    message.includes('wall thickness') ||
    message.includes('bore diameter') ||
    message.includes('corner radius') ||
    message.includes('fillet') ||
    message.includes('chamfer') ||
    message.includes('inner diameter') ||
    message.includes('smaller than') ||
    message.includes('less than') ||
    message.includes('must be') ||
    message.includes('Please specify') ||
    message.includes('Please provide') ||
    message.includes('What ') ||
    message.includes('interpreter') ||
    message.includes('Gemini API key') ||
    message.includes('conversation') ||
    message.includes('not found') ||
    message.includes('sign-in') ||
    message.includes('password')
  ) {
    return message;
  }

  // Unsupported shape / local parser limitation
  if (
    message.includes('supported shape') ||
    message.includes('local interpreter') ||
    message.includes('simplify the geometry') ||
    message.includes('local parser') ||
    message.includes('could not identify') ||
    message.includes('I could not identify')
  ) {
    return message + ' Try a cylinder, box, sphere, cone, tube/ring, or select Gemini.';
  }

  // Clarification prompts from the parser
  if (message.startsWith('Please ') || message.includes('clarify')) {
    return message;
  }

  // Gemini-specific failures
  if (message.includes('Gemini') || message.includes('CadQuery script')) {
    if (message.includes('timed out') || message.includes('timeout')) {
      return 'Model generation took too long. Try a simpler shape or fewer features.';
    }
    if (message.includes('quota') || message.includes('rate limit')) {
      return 'Gemini usage limit reached. Please wait a moment and try again.';
    }
    if (message.includes('API key') || message.includes('authentication') || message.includes('permission')) {
      return 'Gemini is not available — check the server API key configuration.';
    }
    if (message.includes('could not produce') || message.includes('no script') || message.includes('invalid')) {
      return "I couldn't produce a valid model from that request. Try clarifying the dimensions or simplifying the shape.";
    }
    if (message.includes('unavailable') || message.includes('server error')) {
      return 'Gemini is temporarily unavailable. Please try again in a moment.';
    }
    return message;
  }

  // Sandbox / resource errors
  if (status === 503) {
    if (message.includes('CPU') || message.includes('cpu') || message.includes('time budget')) {
      return 'Model generation took too long. Try a simpler shape or fewer features.';
    }
    if (message.includes('memory')) {
      return 'The model was too complex to generate. Try smaller dimensions or fewer features.';
    }
    if (message.includes('sandbox') || message.includes('worker')) {
      return 'The geometry engine encountered an internal error. Please try again.';
    }
    if (message.includes('network') || message.includes('connect')) {
      return 'Could not reach the Gemini service. Check your internet connection.';
    }
    return 'The server is temporarily unavailable. Please try again in a moment.';
  }

  // Geometry validation errors
  if (status === 422) {
    if (message.includes('CAD dimensions differ')) {
      return "The generated geometry didn't match the requested dimensions. Try rephrasing or use Gemini.";
    }
    if (message.includes('empty') || message.includes('invalid') || message.includes('degenerate')) {
      return "The requested geometry couldn't be built. Check the dimensions make physical sense.";
    }
    if (message.includes('watertight') || message.includes('manifold')) {
      return "The generated model has mesh issues. Try a simpler shape or use Gemini.";
    }
    return message;
  }

  // Rate limiting
  if (status === 429) {
    return 'Too many requests. Please wait a moment and try again.';
  }

  // Auth errors
  if (status === 401 || status === 403) {
    return message || 'Authentication required. Please sign in again.';
  }

  // Generic fallback preserves the original message
  return message || `Request failed (${status}).`;
}

export async function api<T>(url:string,body?:unknown,method?:string):Promise<T>{
 const verb=method||(body===undefined?'GET':'POST');
 const headers:Record<string,string>={};
 if(body!==undefined)headers['Content-Type']='application/json';
 if(verb!=='GET'&&csrfToken)headers['X-CSRF-Token']=csrfToken;
 const response=await fetch(url,{method:verb,headers,credentials:'include',...(body===undefined?{}:{body:JSON.stringify(body)})});
 if(!response.ok){
   const payload=await response.json().catch(()=>null);
   const raw=payload?.detail?.message||`Request failed (${response.status}).`;
   throw new Error(friendlyError(raw, response.status));
 }
 if(response.status===204)return undefined as T;
 return response.json();
}
