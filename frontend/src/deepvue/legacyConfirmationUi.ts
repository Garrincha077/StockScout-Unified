import {fieldDefs} from './filterEngine.ts'

const STATUS_FIELD='legacyConfirmationStatus'
const REASON_FIELD='legacyConfirmationReasons'

export function installLegacyConfirmationFields(){
  if(!fieldDefs.some(field=>field.id===STATUS_FIELD)){
    fieldDefs.push({
      id:STATUS_FIELD,
      label:'LEGACY confirmation',
      kind:'text',
      defaultOp:'=',
      placeholder:'CONFIRMED',
    })
  }
  if(!fieldDefs.some(field=>field.id===REASON_FIELD)){
    fieldDefs.push({
      id:REASON_FIELD,
      label:'LEGACY confirmation reason',
      kind:'text',
      defaultOp:'contains',
      placeholder:'ORIGINAL_RUN_BUY',
    })
  }
}

installLegacyConfirmationFields()
