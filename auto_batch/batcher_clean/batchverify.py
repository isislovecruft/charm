# Entry point to executing the batcher algorithm. It takes in an input bv file
# which describes the verification equation, variable types, components of the public, signature
# and message. Finally, an optional transform section to describe the specific order in which
# to apply the techniques 2, 3a, 3b and 4.

import sdlpath
from sdlparser.SDLParser import *
from batchtechniques import *
from batchproof import *
from batchconfig import *
from batchorder import BatchOrder
from batchcomboeq import TestForMultipleEq,CombineMultipleEq,SmallExpTestMul,AfterTech2AddIndex,UpdateDeltaIndex
from batchsyntax import BasicTypeExist,PairingTypeCheck
from batchoptimizer import PairInstanceFinderImproved
from loopunroll import *
from benchmark_interface import getBenchmarkInfo
from constructbatch import *

debug = False
THRESHOLD_FLAG = CODEGEN_FLAG = PRECOMP_CHECK = VERBOSE = CHOOSE_STRATEGY = False
global_count   = 0
flags = { 'multiple':None, 'step1':None }
singleVE = True # flag to define whether working with a single or multi-eq for verification
filePrefix = None
crypto_library = curve = param_id = None

def handleVerifyEq(equation, index, verbose):
    global singleVE, flags
    VERBOSE = verbose
#    print("Input: ", Type(equation), equation)
    combined_equation = BinaryNode.copy(equation.right)
    if VERBOSE: print("Original eq:", combined_equation)
    tme = TestForMultipleEq()
    ASTVisitor(tme).preorder(combined_equation)
    flags['multiple' + str(index)] = False
    if tme.multiple:
        singleVE = False
        cme = CombineMultipleEq()
        ASTVisitor(cme).preorder(combined_equation)
        if len(cme.finalAND) == 1: 
            combined_equation = cme.finalAND.pop()
            if VERBOSE: print("Final combined eq: ", combined_equation)
            se_test = SmallExpTestMul()
            combined_equation2 = BinaryNode.copy(combined_equation)
            ASTVisitor(se_test).preorder(combined_equation2)            
            flags['multiple' + str(index)] = True
            flags[ str(index) ] = combined_equation2
            flags[ 'verify' + str(index) ] = equation.right # used for verify in tex
            # this is step0 for multi equation case
            flags[ 'step1' ] = combined_equation2 # add delta index #s here
            if VERBOSE: print("delta eq: ", combined_equation2)
#            sys.exit("Testing stuff!!")
        else:
            # may need to combine them further? or batch separaely
            print("Note: multiple equations left. Either batch each equation separately OR combine further.")
            if len(cme.finalAND) == 2:
                combined_equation2 = BinaryNode(ops.AND, cme.finalAND[0], cme.finalAND[1])
                cme2 = CombineMultipleEq(addIndex=False)
                ASTVisitor(cme2).preorder(combined_equation2)
                combined = cme2.finalAND.pop()
                if VERBOSE: print("Combined: ", combined)
                se_test = SmallExpTestMul()
                combined2 = BinaryNode.copy(combined)
                ASTVisitor(se_test).preorder(combined2)
                if VERBOSE: print("combined: ", combined2)               
#                exit(0)
                flags['multiple' + str(index)] = True
                flags[ str(index) ] = combined2
                flags[ 'verify' + str(index) ] = equation.right # used for verify in tex        
                flags[ 'step1' ] = combined2 # add delta index #s here
                return combined
#            sys.exit("Testing Stuff 2!!!")

            return cme.finalAND
    return combined_equation

def countInstances(equation):
    Instfind = ExpInstanceFinder()
    ASTVisitor(Instfind).preorder(equation)
    if VERBOSE: print("Instances found =>", Instfind.instance, "\n")
    return Instfind.instance

def isOptimized(data):
    # check for counts greater than 1
    for i in data.keys():
        for j in data[i]:
            # if condition is true, then more optimizations are possible
            # effectively, a ^ b occurs more than once
            if data[i][j] > 1: return False
    return True

def checkForSigs(node):
    if node == None: return None
    if Type(node) == ops.HASH:
        return checkForSigs(node.left)
    elif Type(node) == ops.ATTR:
        if node.attr_index and 'z' in node.attr_index: return True
        else: return False
    else: # not sure about this but will see
        result = checkForSigs(node.left)
        if result: return result
        result = checkForSigs(node.right)
        return result

def benchIndivVerification(N, equation, sdl_dict, vars, precompute, _verbose):
    rop_ind = RecordOperations(vars)
    # add attrIndex to non constants
    ASTVisitor(ASTIndexForIndiv(sdl_dict, vars)).preorder(equation)
    if VERBOSE: print("Final indiv eq:", equation, "\n")
    if _verbose:
        print("<====\tINDIVIDUAL\t====>")
        print("vars =>", vars)
        print("Equation =>", equation)
    data = {'key':['N'], 'N': N }

    rop_ind.visit(equation, data)
    if _verbose: print("<===\tOperations count\t===>")
    for i in precompute.keys():
            # if a str, then was precompute introduced programmatically and should skip for individual verification case. 
            if checkForSigs(precompute[i]): data = {'key':['N'], 'N': N }
            else: data = {}            
            rop_ind.visit(precompute[i], data)
            if _verbose: print("Precompute:", i, ":=", precompute[i])
    if _verbose:
        print_results(rop_ind.ops)
    return calculate_times(rop_ind.ops, curve[param_id], N)
    

def benchBatchVerification(N, equation, sdl_dict, vars, precompute, _verbose):
    rop_batch = RecordOperations(vars)
    rop_batch.visit(equation, {})
    if _verbose:
        print("<====\tBATCH\t====>")
        print("Equation =>", equation)
        print("<===\tOperations count\t===>")
    for i in precompute.keys():
        if type(i) != str:
            if checkForSigs(precompute[i]): data = {'key':['N'], 'N': N }
            else: data = {}
            rop_batch.visit(precompute[i], data)
            if _verbose: print("Precompute:", i, ":=", precompute[i])
        else:
            if i == 'delta': # estimate cost of random small exponents
                rop_batch.ops['prng'] += N
                if _verbose: print("Precompute:", i, ":=", precompute[i])
            else:  # estimate cost of some precomputations
                bp = SDLParser()
                index = BinaryNode( i )
                if 'j' in index.attr_index:
                    compute = bp.parse( "for{z:=1, N} do " + precompute[i] )
                    rop_batch.visit(compute, {})
                    if _verbose: print("Precompute:", i, ":=", compute)
                else:
                    if _verbose: print("TODO: need to account for this: ", i, ":=", precompute[i])
    if _verbose:
        print_results(rop_batch.ops)
    return calculate_times(rop_batch.ops, curve[param_id], N)

def writeFile(file_name, file_contents):
     f = open(file_name, 'w')
     f.write(file_contents)
     f.close()
 
def runBatcher(opts, proofGen, file, verify, ast_struct, eq_number=0):
    global PROOFGEN_FLAG, THRESHOLD_FLAG, CODEGEN_FLAG, PRECOMP_CHECK, VERBOSE, CHOOSE_STRATEGY
    global global_count, flags, singleVE
    PROOFGEN_FLAG, THRESHOLD_FLAG, CODEGEN_FLAG, PRECOMP_CHECK = opts['proof'], opts['threshold'], opts['codegen'], opts['pre_check']
    VERBOSE, CHOOSE_STRATEGY = opts['verbose'], opts['strategy']
    SDL_OUT_FILE = opts['out_file']
    constants, types = ast_struct[ CONST ], ast_struct[ TYPE ]
    latex_subs = ast_struct[ LATEX ]
    if ast_struct.get(PRECOMP):
        (indiv_precompute, batch_precompute) = ast_struct[ PRECOMP ]
    else:
        (indiv_precompute, batch_precompute) = {}, {}
    batch_precompute[ "delta" ] = "for{z := 1, N} do prng_z"
    
    algorithm = ast_struct [ TRANSFORM ]
    FIND_ORDER     = False
    if not algorithm: FIND_ORDER = True

    N = None
    setting = {}
    metadata = {}
    for n in ast_struct[ OTHER ]:
        if 'verify' in str(n.left):
            pass
        elif str(n.left) == 'N':
            N = int(str(n.right))
            metadata['N'] = str(n.right)
        elif str(n.left) in [SIGNATURE, PUBLIC, MESSAGE]:
            setting[ str(n.left) ] = str(n.right)
        else:
            metadata[ str(n.left) ] = str(n.right)
    
    sig_vars, pub_vars, msg_vars = ast_struct[ SIGNATURE ], ast_struct[ PUBLIC ], ast_struct[ MESSAGE ]
    batch_count = {} # F = more than one, T = only one exists
    MSG_set = setting.get(MESSAGE)
    PUB_set = setting.get(PUBLIC)
    SIG_set = setting.get(SIGNATURE)
    if MSG_set == SAME:
        batch_count[ MESSAGE ] = SAME 
    elif MSG_set in metadata.keys():
        checkDotProd = CheckExistingDotProduct(MSG_set)
        ASTVisitor(checkDotProd).preorder(verify)
        if not checkDotProd.applied:
            batch_count[ MESSAGE ] = MSG_set
        else:
            batch_count[ MESSAGE ] = None
    else:
        print("variable not defined but referenced: ", MSG_set)
    
    # check public key setting (can either be many keys or just one single key)
    if PUB_set == SAME:
        batch_count[ PUBLIC ] = SAME 
    elif PUB_set in metadata.keys():
        checkDotProd = CheckExistingDotProduct(PUB_set)
        ASTVisitor(checkDotProd).preorder(verify)
        if not checkDotProd.applied:
            batch_count[ PUBLIC ] = PUB_set
        else:
            batch_count[ PUBLIC ] = None
        
    else:
        print("variable not defined but referenced: ", PUB_set)
    
    if SIG_set in metadata.keys():
        batch_count[ SIGNATURE ] = SIG_set
    else:
        print("variable not defined but referenced: ", SIG_set)    
    
    if VERBOSE: print("setting: ", batch_count)
    
    vars = types
    vars['N'] = N
    vars.update(metadata)
    if VERBOSE: print("variables =>", vars)
    if VERBOSE: print("metadata =>", metadata)

    # build data inputs for technique classes    
    sdl_data = { CONST : constants, PUBLIC: pub_vars, MESSAGE : msg_vars, SETTING : batch_count } 
    if PROOFGEN_FLAG:
        # start the LCG
        proofGen.initLCG(constants, vars, sig_vars, latex_subs)
        if flags['step1']: proofGen.setStepOne(flags['step1'])

    techniques = {'2':Technique2, '3':Technique3, '4':Technique4, '5':DotProdInstanceFinder, '6':PairInstanceFinder, '7':Technique7, '8':Technique8 }
    #print("VERIFY EQUATION =>", verify)
    if PROOFGEN_FLAG: 
#        lcg_data[ lcg_steps ] = { 'msg':'Equation', 'eq': lcg.print_statement(verify) }
        if flags['multiple' + str(eq_number)]: 
#            lcg_data[ lcg_steps ]['eq'] = lcg.print_statement(flags[ str(eq_number) ]) # shortcut!
#            print("JAA => EQUATIONS: ", lcg.print_statement(flags[ 'verify' + str(eq_number) ]))
            proofGen.setIndVerifyEq(flags[ 'verify' + str(eq_number) ])
        else:
            proofGen.setIndVerifyEq( verify )
#        lcg_steps += 1
        
    verify2 = BinaryNode.copy(verify)
#    ASTVisitor(CombineVerifyEq(const, vars)).preorder(verify2.right)
    ASTVisitor(CVForMultiSigner(vars, sig_vars, pub_vars, msg_vars, batch_count)).preorder(verify2)
    if PROOFGEN_FLAG: 
        proofGen.setNextStep( 'consolidate', verify2 )
    # check whether this step is necessary!    
    verify_test = BinaryNode.copy(verify2)
    pif = PairInstanceFinder()
    ASTVisitor(pif).preorder(verify_test)
    if pif.testForApplication(): # if we can combine some pairings, then no need to distribute just yet
        pass
    else:
        ASTVisitor(SimplifyDotProducts()).preorder(verify2)

    if VERBOSE: print("\nStage A: Combined Equation =>", verify2)
    ASTVisitor(SmallExponent(constants, vars)).preorder(verify2)
    if VERBOSE: print("\nStage B: Small Exp Test =>", verify2, "\n")
    if PROOFGEN_FLAG: 
        proofGen.setNextStep( 'smallexponents', verify2 )

    # figure out order automatically (if not specified in bv file)
    if FIND_ORDER:
        result = BatchOrder(sdl_data, types, vars, BinaryNode.copy(verify2), crypto_library).strategy()
        algorithm = [str(x) for x in result]
        print("<== Found Batch Algorithm ==>", algorithm)

    # execute the batch algorithm sequence 
    for option in algorithm:
        if option == '5':
            option_str = "Simplifying =>"
            Tech = techniques[option]()
        elif option == '6':
            option_str = "Combine Pairings:"
            Tech = techniques[option]()            
        elif option in techniques.keys():
            option_str = "Applying technique " + option
            Tech = techniques[option](sdl_data, vars, metadata)
        else:
            print("Unrecognized technique selection.")
            continue
        ASTVisitor(Tech).preorder(verify2)
        if option == '2' and not singleVE:
            # add index numbers to deltas if dealing with multiple verification equations
            aftTech2 = AfterTech2AddIndex()
            ASTVisitor(aftTech2).preorder(verify2)
        elif option == '6':
            testVerify2 = Tech.makeSubstitution(verify2)
            if testVerify2 != None: verify2 = testVerify2
        if hasattr(Tech, 'precompute'):
            batch_precompute.update(Tech.precompute)
        if VERBOSE:
           print(Tech.rule, "\n")
           print(option_str, ":",verify2, "\n")
        if PROOFGEN_FLAG:
            proofGen.setNextStep(Tech.rule, verify2)
    
    # now we check if Technique 10 is applicable (aka loop unrolling)
    Tech10 = Technique10(sdl_data, vars, metadata)
    ASTVisitor(Tech10).preorder(verify2)
    
    if Tech10.testForApplication():
        evalint = EvaluateAtIntValue(Tech10.for_iterator, Tech10.for_start)
        testEq = BinaryNode.copy(Tech10.loopStmt)
        ASTVisitor(evalint).preorder(testEq)
        print("Evaluated version at %d: %s" % (Tech10.for_start, testEq))
        print("Combine the rest into this one...")
        for t in range(Tech10.for_start+1, Tech10.for_end):
            evalint = EvaluateAtIntValue(Tech10.for_iterator, t)  
            testEq2 = BinaryNode.copy(Tech10.loopStmt)
            ASTVisitor(evalint).preorder(testEq2)
            print("Eval-n-Combine version at %d: %s" % (t, testEq2))
            # Combine testEq2 into testEq! Need a class to do this for me.
        
#        sys.exit("DONE TESTING!")
    
    if PROOFGEN_FLAG:
        proofGen.setNextStep('finalbatcheq', None)
        
    if PRECOMP_CHECK:
        countDict = countInstances(verify2) 
        if not isOptimized(countDict):
            ASTVisitor(SubstituteExps(countDict, batch_precompute, vars)).preorder(verify2)
            print("Final batch eq:", verify2)
        else:
            print("Final batch eq:", verify2)

    # START BENCHMARK : THRESHOLD ESTIMATOR
    if THRESHOLD_FLAG:
        print("<== Running threshold estimator ==>")
        (indiv_msmt, indiv_avg_msmt) = benchIndivVerification(N, verify, sdl_data, vars, indiv_precompute, VERBOSE)
        print("Result N =",N, ":", indiv_avg_msmt)

        outfile = file.split('.bv')[0]
        indiv, batch = outfile + "_indiv.dat", outfile + "_batch.dat"
        if filePrefix: indiv = filePrefix + indiv; batch = filePrefix + batch # redirect output file
    
        output_indiv = open(indiv, 'w'); output_batch = open(batch, 'w')
        threshold = -1
        for i in range(1, N+1):
            vars['N'] = i
            (batch_msmt, batch_avg_msmt) = benchBatchVerification(i, verify2, sdl_data, vars, batch_precompute, VERBOSE)
            output_indiv.write(str(i) + " " + str(indiv_avg_msmt) + "\n")
            output_batch.write(str(i) + " " + str(batch_avg_msmt) + "\n")
            if batch_avg_msmt <= indiv_avg_msmt and threshold == -1: threshold = i 
        output_indiv.close(); output_batch.close()
        print("Result N =",N, ":", batch_avg_msmt)
        print("Threshold: ", threshold)
    # STOP BENCHMARK : THRESHOLD ESTIMATOR 
    # TODO: check avg for when batch is more efficient than 
    if CODEGEN_FLAG:
        print("Final batch eq:", verify2)
        subProds = SubstituteSigDotProds(vars, 'z', 'N', global_count)
        ASTVisitor(subProds).preorder(verify2)
        # update variable counter
        global_count = subProds.cnt
        # print("Dot prod =>", subProds.dotprod)
        # need to check for presence of other variables
#        key = None
#        for i in metadata.keys():
#            if i != 'N': key = i
        subProds1 = SubstituteSigDotProds(vars, 'y', 'l', global_count)
        global_count = subProds1.cnt
#        subProds1.setState(subProds.cnt)
        ASTVisitor(subProds1).preorder(verify2)
        if VERBOSE:  
          print("<====\tPREP FOR CODE GEN\t====>")
          print("\nFinal version =>", verify2, "\n")
        out_str = "batch eq := { %s }\n" % verify2
        for i in subProds.dotprod['list']:
            if VERBOSE: print("compute: ", i,":=", subProds.dotprod['dict'][i])    
            out_str += "%s := %s\n" % (i, subProds.dotprod['dict'][i])
        for i in subProds1.dotprod['list']:
            if VERBOSE: print("compute: ", i,":=", subProds1.dotprod['dict'][i])
            out_str += "%s := %s\n" % (i, subProds1.dotprod['dict'][i])              
        for i in batch_precompute.keys():
            if VERBOSE: print("precompute:", i, ":=", batch_precompute[i])
            out_str += "precompute: %s := %s\n" % (i, batch_precompute[i])                            
        for i in subProds.dotprod['list']:
            if VERBOSE: print(i,":=", subProds.dotprod['types'][i])
            out_str += "%s := %s\n" % (i, subProds.dotprod['types'][i])              
        for i in subProds1.dotprod['list']:
            if VERBOSE: print(i,":=", subProds1.dotprod['types'][i])
            out_str += "%s := %s\n" % (i, subProds1.dotprod['types'][i])
#          print(out_str)
        if SDL_OUT_FILE != None:
            print("Writing partial SDL: ", SDL_OUT_FILE)
            writeFile(SDL_OUT_FILE, out_str)

    if PROOFGEN_FLAG:
        latex_file = metadata['name'].upper() + str(eq_number)
        print("Generated the proof written to file: verification_gen%s.tex" % latex_file)
        proofGen.compileProof(latex_file)
        

def runBatcher2(opts, proofGen, file, verify, settingObj, loopDetails, eq_number=0):
    global PROOFGEN_FLAG, THRESHOLD_FLAG, CODEGEN_FLAG, PRECOMP_CHECK, VERBOSE, CHOOSE_STRATEGY
    global global_count, flags, singleVE
    PROOFGEN_FLAG, THRESHOLD_FLAG, CODEGEN_FLAG, PRECOMP_CHECK = opts['proof'], opts['threshold'], opts['codegen'], opts['pre_check']
    VERBOSE, CHOOSE_STRATEGY = opts['verbose'], opts['strategy']
    SDL_OUT_FILE = opts['out_file']
    constants, types = settingObj.getConstantVars(), settingObj.getTypes()
    sigVars, pubVars, msgVars = settingObj.getSignatureVars(), settingObj.getPublicVars(), settingObj.getMessageVars()
    latex_subs = settingObj.getLatexVars()

    if settingObj.getPrecomputeVars():
        (indiv_precompute, batch_precompute) = settingObj.getPrecomputeVars()
    else:
        (indiv_precompute, batch_precompute) = {}, {}
    batch_precompute[ "delta" ] = "for{z := 1, N} do prng_z"
    algorithm = settingObj.getTransformList()
    FIND_ORDER     = False
    if not algorithm: FIND_ORDER = True

    N = settingObj.getNumSignatures()    
#    sig_vars, pub_vars, msg_vars = ast_struct[ SIGNATURE ], ast_struct[ PUBLIC ], ast_struct[ MESSAGE ]
    setting = settingObj.getBatchCount()
    batch_count = {} # F = more than one, T = only one exists
    MSG_set = setting[MSG_CNT]
    PUB_set = setting[PUB_CNT]
    SIG_set = setting[SIG_CNT]
    if MSG_set == SAME:
        batch_count[ MESSAGE ] = SAME 
    elif MSG_set in types.keys(): # where N is defined
        checkDotProd = CheckExistingDotProduct(MSG_set)
        ASTVisitor(checkDotProd).preorder(verify)
        if not checkDotProd.applied:
            batch_count[ MESSAGE ] = MSG_set
        else:
            batch_count[ MESSAGE ] = None
    else:
        print("variable not defined but referenced: ", MSG_set)
    
    # check public key setting (can either be many keys or just one single key)
    if PUB_set == SAME:
        batch_count[ PUBLIC ] = SAME 
    elif PUB_set in types.keys():
        checkDotProd = CheckExistingDotProduct(PUB_set)
        ASTVisitor(checkDotProd).preorder(verify)
        if not checkDotProd.applied:
            batch_count[ PUBLIC ] = PUB_set
        else:
            batch_count[ PUBLIC ] = None
        
    else:
        print("variable not defined but referenced: ", PUB_set)
    
    if SIG_set in types.keys():
        batch_count[ SIGNATURE ] = SIG_set
    else:
        print("variable not defined but referenced: ", SIG_set)    
    
    if VERBOSE: print("setting: ", batch_count)
    
    if VERBOSE: print("variables =>", types)
    # build data inputs for technique classes    
    sdl_data = { SECPARAM: settingObj.getSecParam(), CONST : constants, PUBLIC: pubVars, MESSAGE : msgVars, SETTING : batch_count, BATCH_VERIFY:settingObj.getVerifyInputArgs(), BATCH_VERIFY_MAP:settingObj.getVerifyInputArgsMap() } 
    if PROOFGEN_FLAG:
        # start the LCG
        proofGen.initLCG(constants, types, sigVars, latex_subs)
        if flags['step1']: proofGen.setStepOne(flags['step1'])

    techniques = {'2':Technique2, '3':Technique3, '4':Technique4, '5':DotProdInstanceFinder, '6':PairInstanceFinder, '7':Technique7, '8':Technique8 }
    #print("VERIFY EQUATION =>", verify)
    if PROOFGEN_FLAG: 
        if flags['multiple' + str(eq_number)]: 
            proofGen.setIndVerifyEq(flags[ 'verify' + str(eq_number) ])
        else:
            proofGen.setIndVerifyEq( verify )
        
    verify2 = BinaryNode.copy(verify)
    ASTVisitor(CVForMultiSigner(types, sigVars, pubVars, msgVars, batch_count)).preorder(verify2)
    if PROOFGEN_FLAG: 
        proofGen.setNextStep( 'consolidate', verify2 )
    # check whether this step is necessary!    
    verify_test = BinaryNode.copy(verify2)
    pif = PairInstanceFinder()
    ASTVisitor(pif).preorder(verify_test)
    if pif.testForApplication(): # if we can combine some pairings, then no need to distribute just yet
        pass
    else:
        ASTVisitor(SimplifyDotProducts()).preorder(verify2)

    if VERBOSE: print("\nStage A: Combined Equation =>", verify2)
    ASTVisitor(SmallExponent(constants, vars)).preorder(verify2)
    if VERBOSE: print("\nStage B: Small Exp Test =>", verify2, "\n")
    if PROOFGEN_FLAG: 
        proofGen.setNextStep( 'smallexponents', verify2 )
        
    # figure out order automatically (if not specified in bv file)
    if FIND_ORDER:
        result = BatchOrder(sdl_data, types, BinaryNode.copy(verify2), crypto_library).strategy()
        algorithm = [str(x) for x in result]
        print("<== Found Batch Algorithm ==>", algorithm)

    # execute the batch algorithm sequence 
    for option in algorithm:
        if option == '5':
            option_str = "Simplifying =>"
            Tech = techniques[option]()
        elif option == '6':
            option_str = "Combine Pairings:"
            Tech = techniques[option]()            
        elif option in techniques.keys():
            option_str = "Applying technique " + option
            Tech = techniques[option](sdl_data, types)
        else:
            print("Unrecognized technique selection.")
            continue
        ASTVisitor(Tech).preorder(verify2)
        if option == '2' and not singleVE:
            # add index numbers to deltas if dealing with multiple verification equations
            aftTech2 = UpdateDeltaIndex()
            ASTVisitor(aftTech2).preorder(verify2)  
        elif option == '6':
            testVerify2 = Tech.makeSubstitution(verify2)
            if testVerify2 != None: verify2 = testVerify2
        if hasattr(Tech, 'precompute'):
            batch_precompute.update(Tech.precompute)
        if VERBOSE:
           print(Tech.rule, "\n")
           print(option_str, ":",verify2, "\n")
        if PROOFGEN_FLAG:
            proofGen.setNextStep(Tech.rule, verify2)
    
    # now we check if Technique 10 is applicable (aka loop unrolling)
    Tech10 = Technique10(sdl_data, types, loopDetails)
    
    if Tech10.testForApplication():
        verify2 = Tech10.makeSubsitution(verify2)
        if VERBOSE: print(Tech10.rule, ":", verify2, "\n")
        if PROOFGEN_FLAG:
            proofGen.setNextStep(Tech10.rule, verify2)
    ##################################################################
            
            # Combine testEq2 into testEq! Need a class to do this for me.
        
#        sys.exit("DONE TESTING!")
    
    if PROOFGEN_FLAG:
        proofGen.setNextStep('finalbatcheq', None)
        
    if PRECOMP_CHECK:
        countDict = countInstances(verify2) 
        if not isOptimized(countDict):
            ASTVisitor(SubstituteExps(countDict, batch_precompute, types)).preorder(verify2)
            print("Final batch eq:", verify2)
        else:
            print("Final batch eq:", verify2)

    # START BENCHMARK : THRESHOLD ESTIMATOR
    if THRESHOLD_FLAG:
        print("<== Running threshold estimator ==>")
        (indiv_msmt, indiv_avg_msmt) = benchIndivVerification(N, verify, sdl_data, types, indiv_precompute, VERBOSE)
        print("Result N =",N, ":", indiv_avg_msmt)

        outfile = file.split('.bv')[0]
        indiv, batch = outfile + "_indiv.dat", outfile + "_batch.dat"
        if filePrefix: indiv = filePrefix + indiv; batch = filePrefix + batch # redirect output file
    
        output_indiv = open(indiv, 'w'); output_batch = open(batch, 'w')
        threshold = -1
        for i in range(1, N+1):
            types['N'] = i
            (batch_msmt, batch_avg_msmt) = benchBatchVerification(i, verify2, sdl_data, types, batch_precompute, VERBOSE)
            output_indiv.write(str(i) + " " + str(indiv_avg_msmt) + "\n")
            output_batch.write(str(i) + " " + str(batch_avg_msmt) + "\n")
            if batch_avg_msmt <= indiv_avg_msmt and threshold == -1: threshold = i 
        output_indiv.close(); output_batch.close()
        print("Result N =",N, ":", batch_avg_msmt)
        print("Threshold: ", threshold)
    # STOP BENCHMARK : THRESHOLD ESTIMATOR 
    # TODO: check avg for when batch is more efficient than 
    
##    if eq_number > 0: suffix = str(eq_number)
##    else: suffix = ""
##    if SDL_OUT_FILE == None: SDL_OUT_FILE = types['name'] + suffix
##    sdlBatch = SDLBatch(SDL_OUT_FILE, sdl_data, types, verify2, batch_precompute, global_count)
##    sdlBatch.construct(VERBOSE)
##    global_count = sdlBatch.getVariableCount()
    
    if PROOFGEN_FLAG:
        latex_file = types['name'].upper() + str(eq_number)
        print("Generated the proof written to file: verification_gen%s.tex" % latex_file)
        proofGen.compileProof(latex_file)
    return (SDL_OUT_FILE, sdl_data, types, verify2, batch_precompute, global_count)
    
""" 
    if CODEGEN_FLAG:
        print("Final batch eq:", verify2)
        subProds = SubstituteSigDotProds(types, 'z', 'N', global_count)
        ASTVisitor(subProds).preorder(verify2)
        # update variable counter
        global_count = subProds.cnt
        # print("Dot prod =>", subProds.dotprod)
        # need to check for presence of other variables
#        key = None
#        for i in metadata.keys():
#            if i != 'N': key = i

        subProds1 = SubstituteSigDotProds(types, 'y', 'l', global_count)
        global_count = subProds1.cnt
#        subProds1.setState(subProds.cnt)
        ASTVisitor(subProds1).preorder(verify2)
        if VERBOSE:  
          print("<====\tPREP FOR CODE GEN\t====>")
          print("\nFinal version =>", verify2, "\n")
        out_str = "batch eq := { %s }\n" % verify2
        for i in subProds.dotprod['list']:
            if VERBOSE: print("compute: ", i,":=", subProds.dotprod['dict'][i])    
            out_str += "%s := %s\n" % (i, subProds.dotprod['dict'][i])
 #        for i in subProds1.dotprod['list']:
 #            if VERBOSE: print("compute: ", i,":=", subProds1.dotprod['dict'][i])
 #            out_str += "%s := %s\n" % (i, subProds1.dotprod['dict'][i])              
 #        for i in batch_precompute.keys():
 #            if VERBOSE: print("precompute:", i, ":=", batch_precompute[i])
 #            out_str += "precompute: %s := %s\n" % (i, batch_precompute[i])
 #        for i in subProds.dotprod['list']:
 #            if VERBOSE: print(i,":=", subProds.dotprod['types'][i])
 #            out_str += "%s := %s\n" % (i, subProds.dotprod['types'][i])              
 #        for i in subProds1.dotprod['list']:
 #            if VERBOSE: print(i,":=", subProds1.dotprod['types'][i])
 #            out_str += "%s := %s\n" % (i, subProds1.dotprod['types'][i])
 #          print(out_str)
 #        if SDL_OUT_FILE != None:
 #            print("Writing partial SDL: ", SDL_OUT_FILE)
 #            writeFile(SDL_OUT_FILE, out_str)

def benchmark_batcherOLD(argv, prefix=None):
    global THRESHOLD_FLAG, PROOFGEN_FLAG, PRECOMP_CHECK, VERBOSE, CHOOSE_STRATEGY
    global filePrefix
    ast_struct = None
    THRESHOLD_FLAG = PROOFGEN_FLAG = PRECOMP_CHECK = VERBOSE = CHOOSE_STRATEGY = False
    
    # main for batch input parser    
    print(argv)
    file = argv[1]
    if prefix: filePrefix = prefix
    for i in argv:
        if i == "-b": THRESHOLD_FLAG = True
        elif i == "-v": VERBOSE = True
        elif i == "-p": PROOFGEN_FLAG = True
        elif i == "-s": CHOOSE_STRATEGY = True
    ast_struct = parseFile(file)
    
    verify_eq, N = [], None; cnt = 0
    for n in ast_struct[ OTHER ]:
        if 'verify' in str(n.left):
            result = handleVerifyEq(n, cnt); cnt += 1
            if type(result) != list: verify_eq.append(result)
            else: verify_eq.extend(result)

    # verify 
    variables = ast_struct[ TYPE ]
    for eq in verify_eq:
        bte = BasicTypeExist( variables )
        ASTVisitor( bte ).preorder( eq )
        bte.report( eq )
        
        cte = PairingTypeCheck( variables )
        ASTVisitor( cte ).preorder( eq )
        cte.report( eq )

    # process settings
    for i in range(len(verify_eq)):    
        runBatcher(file + str(i), verify_eq[i], ast_struct, i)
    return
"""

def buildSDLBatchVerifier(sdlOutFile, sdl_data, types, verify2, batch_precompute, var_count, setting):
    if sdlOutFile == None: sdlOutFile = types['name'] + "-full-batch"
    sdlBatch = SDLBatch(sdlOutFile, sdl_data, types, verify2, batch_precompute, var_count, setting)
    sdlBatch.construct(VERBOSE)
    return sdlBatch.getVariableCount()

def run_main(opts):
    global singleVE, crypto_library, curve, param_id, assignInfo, varTypes, global_count
    verbose   = opts['verbose']
    statement = opts['test_stmt']
    file      = opts['sdl_file']
    crypto_library   = opts['library']
    curve, param_id = getBenchmarkInfo(crypto_library)
    if statement:
        debug = levels.all
        parser = SDLParser()
        final = parser.parse(statement)
        print("Final statement(%s): '%s'" % (type(final), final))
        sys.exit(0)
    else:
        # Parse the SDL file into binary tree
#        ast_struct = parseFile(file)
        parseFile2(file, verbose, ignoreCloudSourcing=True)
        setting = SDLSetting(verbose)
        setting.parse(getAssignInfo(), getVarTypes()) # check for errors and pass on to user before continuing


    # process single or multiple equations
    verify_eq, N = [], None; cnt = 0
    #for n in ast_struct[ OTHER ]:
    if len(setting.getVerifyEq()) == 0:
        sys.exit("Could not locate the individual verification equation. Please edit SDL file.\n");
    
    verifyEqDict = setting.getVerifyEq()
    verifyEqUpdated = {}
    verifyList = list(verifyEqDict.keys())
    verifyList.sort()
    
    for k in verifyList:
#        print("k := ", k, ", v := ", verifyEqDict[k])
        if VERIFY in k:
            result = handleVerifyEq(verifyEqDict[k].get(VERIFY), cnt, verbose)
            cnt += 1 # where we do actual verification on # of eqs
            verifyEqUpdated[ k ] = result

    # santiy checks to verify setting makes sense for given equation 
    variables = setting.getTypes()
    for k,v in verifyEqUpdated.items():
        bte = BasicTypeExist( variables )
        ASTVisitor( bte ).preorder( v )
        bte.report( v )

    # initiate the proof generator    
    print("Single verification equation: ", singleVE)
    genProof = GenerateProof(singleVE)
    # process settings
    i = 0
    finalVerifyList = []
    if len(verifyList) == 2:
        sdl_data = {}
        types = {}
        batch_precompute = {}
        sdlOutFile = None
        for k in verifyList:
            loopDetails = None
            if verifyEqDict[ k ][ hasLoop ]:
                try:
                    endValue = str(eval(verifyEqDict[k][endVal], setting.getTypes()))
                except:
                    print("Could not determine loop end value. Please define: ", verifyEqDict[k][endVal])
                    sys.exit(0)
                loopDetails = (verifyEqDict[k][loopVar], verifyEqDict[k][startVal], endValue) 
            (sdlOutFile, sdl_data0, types0, verify2, batch_precompute0, var_count) = runBatcher2(opts, genProof, file + str(i), verifyEqUpdated[k], setting, loopDetails, i)
            i += 1
#            print("BATCH EQUATION: ", verify2)
            finalVerifyList.append(verify2)
            sdl_data.update(sdl_data0)
            types.update(types0)
            batch_precompute.update(batch_precompute0)

        eq1, eq2 = finalVerifyList
        finalEq = CombineEqWithoutNewDelta(eq1, eq2)
        print("FINAL BATCH EQUATION:\n", finalEq)
        buildSDLBatchVerifier(sdlOutFile, sdl_data, types, finalEq, batch_precompute, global_count, setting)
    else:
        for k in verifyList:
            loopDetails = None
            if verifyEqDict[ k ][ hasLoop ]:
                try:
                    endValue = str(eval(verifyEqDict[k][endVal], setting.getTypes()))
                except:
                    print("Could not determine loop end value. Please define: ", verifyEqDict[k][endVal])
                    sys.exit(0)
                loopDetails = (verifyEqDict[k][loopVar], verifyEqDict[k][startVal], endValue)                        
            (sdlOutFile, sdl_data, types, verify2, batch_precompute, var_count) = runBatcher2(opts, genProof, file, verifyEqUpdated[k], setting, loopDetails)
            buildSDLBatchVerifier(sdlOutFile, sdl_data, types, verify2, batch_precompute, var_count, setting)
            
    return None

