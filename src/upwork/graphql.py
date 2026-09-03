DETAILS_QUERY = """
fragment JobPubOpeningInfoFragment on Job {\n    
ciphertext\n

    id\n 
    type\n 
    access\n
    title\n
    hideBudget\n 
    createdOn\n    
    notSureProjectDuration\n 
    notSureFreelancersToHire\n   
    notSureExperienceLevel\n  
    notSureLocationPreference\n
    premium\n  }\n
    fragment JobPubOpeningSegmentationDataFragment on JobSegmentation {\n   
    customValue\n   
                         label\n   
                           name\n    
                           sortOrder\n    
                           type\n    
                           value\n    
                           skill {\n      
                           description\n      
                           externalLink\n      
                           prettyName\n      
                           skill\n      
                           id\n    
                           }\n  
                           }\n  
                           fragment JobPubOpeningSandDataFragment on SandsData {\n    
                           occupation {\n      
                           freeText\n      
                           ontologyId\n      
                           prefLabel\n      
                           id\n      
                           uid: id\n    
                           }\n    
                           ontologySkills {\n      
                           groupId\n      
                           id\n      
                           freeText\n      
                           prefLabel\n      
                           groupPrefLabel\n      
                           relevance\n    }\n    
                           additionalSkills {\n      
                           groupId\n      
                           id\n      
                           freeText\n      
                           prefLabel\n      
                           relevance\n    
                           }\n  }\n  
                           fragment JobPubOpeningFragment on JobPubOpeningInfo {\n    
                           status\n
                           postedOn\n
                           publishTime\n 
                           sourcingTime\n  
                           startDate\n  
                           deliveryDate\n 
                           workload\n 
                           contractorTier\n
                           description\n  
                           info {\n 
                           ...JobPubOpeningInfoFragment\n    }\n   
                           segmentationData {\n 
                           ...JobPubOpeningSegmentationDataFragment\n    }\n    
                           sandsData {\n  
                           ...JobPubOpeningSandDataFragment\n    }\n   
                           category {\n      
                           name\n   
                           urlSlug\n    }\n   
                           categoryGroup {\n    
                           name\n    
                           urlSlug\n    }\n 
                           budget {\n 
                           amount\n   
                           currencyCode\n  
                           }\n   
                           annotations {\n
                           customFields\n 
                           tags\n    }\n 
                           engagementDuration {\n   
                           label\n  
                           weeks\n    }\n 
                           extendedBudgetInfo {\n     
                           hourlyBudgetMin\n  
                           hourlyBudgetMax\n  
                           hourlyBudgetType\n  
                           }\n    attachments @include(if: $isLoggedIn) 
                           {\n     
                           fileName\n   
                           length\n  
                           uri\n   
                           }\n   
                           clientActivity {\n  
                           lastBuyerActivity\n 
                           totalApplicants\n      totalHired\n      totalInvitedToInterview\n      unansweredInvites\n      invitationsSent\n      numberOfPositionsToHire\n    }\n    deliverables\n    deadline\n    tools {\n      name\n    }\n  }\n  fragment JobPubBuyerInfoFragment on JobPubBuyerInfo {\n    location {\n      offsetFromUtcMillis\n      countryTimezone\n      city\n      country\n    }\n    stats {\n      totalAssignments\n      activeAssignmentsCount\n      hoursCount\n      feedbackCount\n      score\n      totalJobsWithHires\n      totalCharges {\n        amount\n      }\n    }\n    company {\n      name @include(if: $isLoggedIn)\n      companyId @include(if: $isLoggedIn)\n      isEDCReplicated\n      contractDate\n      profile {\n        industry\n        size\n      }\n    }\n    jobs {\n      openCount @include(if: $isLoggedIn)\n      postedCount @include(if: $isLoggedIn)\n      openJobs @include(if: $isLoggedIn) {\n        id\n        uid: id\n        isPtcPrivate\n        ciphertext\n        title\n        type\n      }\n    }\n    avgHourlyJobsRate @include(if: $isLoggedIn) {\n      amount\n    }\n  }\n  fragment JobQualificationsFragment on JobQualifications {\n    countries\n    earnings\n    groupRecno\n    languages\n    localDescription\n    localFlexibilityDescription\n    localMarket\n    minJobSuccessScore\n    minOdeskHours\n    onSiteType\n    prefEnglishSkill\n    regions\n    risingTalent\n    shouldHavePortfolio\n    states\n    tests\n    timezones\n    type\n    locationCheckRequired\n    group {\n      groupId\n      groupLogo\n      groupName\n    }\n    location {\n      city\n      country\n      countryTimezone\n      offsetFromUtcMillis\n      state\n      worldRegion\n    }\n    locations {\n      id\n      type\n    }\n    minHoursWeek @skip(if: $isLoggedIn)\n    readyToStartToday {\n      expiresAt\n    }\n  }\n  fragment JobPubSimilarJobsFragment on PubSimilarJob {\n    id\n    ciphertext\n    title\n    description\n    engagement\n    durationLabel\n    contractorTier\n    type\n    createdOn\n    renewedOn\n    amount {\n      amount\n    }\n    maxAmount {\n      amount\n    }\n    ontologySkills {\n      id\n      prefLabel\n    }\n    hourlyBudgetMin\n    hourlyBudgetMax\n  }\n  query JobPubDetailsQuery($id: ID!, $isLoggedIn: Boolean!) {\n    jobPubDetails(id: $id) {\n      opening {\n        ...JobPubOpeningFragment\n      }\n      qualifications {\n        ...JobQualificationsFragment\n      }\n      buyer {\n        ...JobPubBuyerInfoFragment\n      }\n      similarJobs {\n        ...JobPubSimilarJobsFragment\n      }\n      buyerExtra {\n        isPaymentMethodVerified\n      }\n    }\n  }"""



SEARCH_QUERY= """\n  query VisitorJobSearch($requestVariables: VisitorJobSearchV1Request!) {\n    search {\n      universalSearchNuxt {\n        visitorJobSearchV1(request: $requestVariables) {\n          paging {\n            total\n            offset\n            
count\n          }\n          \n    facets {\n      jobType \n    {\n      key\n      value\n    }\n  \n      workload \n    {\n      key\n      value\n    }\n  \n      
clientHires \n    {\n      key\n      value\n    }\n  \n      durationV3 \n    {\n      key\n      value\n    }\n  \n      amount \n    {\n      key\n      value\n    }\n  \n      contractorTier \n    {\n      key\n      value\n    }\n  \n      contractToHire \n    {\n      key\n      value\n    }\n  \n      \n    }\n  \n          results {\n            id\n            title\n            description\n            relevanceEncoded\n            ontologySkills {\n              uid\n              parentSkillUid\n              prefLabel\n              prettyName: prefLabel\n              freeText\n              highlighted\n            }\n            \n            \n            jobTile {\n              job {\n                id\n                ciphertext: cipherText\n                jobType\n                weeklyRetainerBudget\n                hourlyBudgetMax\n                hourlyBudgetMin\n                hourlyEngagementType\n                contractorTier\n                sourcingTimestamp\n                createTime\n                publishTime\n                \n                hourlyEngagementDuration {\n                  rid\n                  label\n                  weeks\n                  mtime\n                  ctime\n                }\n                fixedPriceAmount {\n                  isoCurrencyCode\n                  amount\n                }\n                fixedPriceEngagementDuration {\n                  id\n                  rid\n                  label\n                  weeks\n                  ctime\n                  mtime\n                }\n              }\n            }\n          }\n        }\n      }\n    }\n  }\n """
