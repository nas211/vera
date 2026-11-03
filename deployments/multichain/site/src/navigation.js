export const ROUTES={overview:'/overview',vault:'/vault',borrow:'/borrow',trading:'/trading',agent:'/repay'};
export const tabForPath=path=>Object.keys(ROUTES).find(key=>ROUTES[key]===path);
export function navigate(path,host=window){
 if(host.location.pathname+host.location.hash!==path)host.history.pushState(null,'',path);
 host.dispatchEvent(new Event('vela:navigate'));
}
