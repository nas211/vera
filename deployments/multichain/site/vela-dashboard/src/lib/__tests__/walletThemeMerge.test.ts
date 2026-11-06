import {expect,it} from 'vitest';
import merge from '../vendor/deepmerge.js';
it('rejects poisoned source and target prototypes while preserving nested theme values',()=>{
 const poison=JSON.parse('{"__proto__":{"polluted":true},"constructor":{"prototype":{"polluted":true}},"colors":{"accent":"lime"}}');
 for(const result of [merge(poison,{colors:{background:'white'}}),merge({colors:{background:'white'}},poison),merge.all([{},poison])]){
  expect(Object.getPrototypeOf(result)).toBe(Object.prototype);expect(result.polluted).toBeUndefined();expect(Object.hasOwn(result,'constructor')).toBe(false);expect(result.colors.accent).toBe('lime');
 }
 expect(merge({x:[1]},{x:[2]}).x).toEqual([1,2]);
});
