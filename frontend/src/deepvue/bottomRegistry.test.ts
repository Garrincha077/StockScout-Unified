import test from'node:test'
import assert from'node:assert/strict'
import{matchesGroups}from'./filterEngine.ts'
import{bottomBuiltInScreens,bottomFieldDefs}from'./bottomRegistry.ts'

test('Bottom owns 19 valid presets backed by its own field registry',()=>{
  assert.equal(bottomBuiltInScreens.length,19)
  const fields=new Set(bottomFieldDefs.map(field=>field.id))
  for(const screen of bottomBuiltInScreens)for(const group of screen.groups)for(const rule of group.rules)assert.ok(fields.has(rule.field),`${screen.name}: ${rule.field}`)
})

test('every curated Bottom preset produces a nonzero fixture result',()=>{
  for(const screen of bottomBuiltInScreens){
    const row:Record<string,unknown>={ticker:screen.name}
    for(const group of screen.groups){
      const selected=group.logic==='ALL'?group.rules:group.rules.slice(0,1)
      for(const rule of selected){
        const definition=bottomFieldDefs.find(field=>field.id===rule.field)!
        if(rule.op==='true'||rule.op==='false')row[rule.field]=rule.op==='true'
        else if(definition.kind==='text')row[rule.field]=rule.value
        else if(rule.op==='between'){const[a,b]=rule.value.split(',').map(Number);row[rule.field]=(a+b)/2}
        else{const value=Number(rule.value);row[rule.field]=rule.op==='>'?value+1:rule.op==='<'?value-1:value}
      }
    }
    assert.ok(matchesGroups(row,screen.groups,screen.rootLogic,bottomFieldDefs),`${screen.name} unexpectedly returned zero rows`)
  }
})
