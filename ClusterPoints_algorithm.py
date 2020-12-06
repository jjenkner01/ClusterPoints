# -*- coding: utf-8 -*-

"""
/***************************************************************************
 ClusterPoints
                                 A QGIS plugin
 Cluster Points conducts spatial clustering of points based on their mutual distance to each other. The user can select between the K-Means algorithm and (agglomerative) hierarchical clustering with several different link functions.
 Generated by Plugin Builder: http://g-sherman.github.io/Qgis-Plugin-Builder/
                              -------------------
        begin                : 2020-03-30
        copyright            : (C) 2020 by Johannes Jenkner
        email                : jjenkner@web.de
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

__author__ = 'Johannes Jenkner'
__date__ = '2020-11-28'
__copyright__ = '(C) 2020 by Johannes Jenkner'

# This will get replaced with a git SHA1 when you do a git archive

__revision__ = '$Format:%H$'



from .cf_blobs import CFTask

from qgis.core import QgsProcessingAlgorithm,QgsApplication,QgsProcessingProvider

from PyQt5.QtCore import QCoreApplication,QVariant

from qgis.core import (QgsField,QgsPoint,QgsPointXY,QgsDistanceArea,
                       QgsProcessingParameterVectorLayer,QgsProcessingParameterBoolean,
                       QgsProcessingParameterEnum,QgsProcessingParameterNumber,
                       QgsProcessingParameterField,QgsVectorLayer,QgsFeature,QgsGeometry)

from qgis.core import (QgsProcessing,QgsProcessingException,QgsProcessingAlgorithm,
                      Qgis,QgsTask,QgsMessageLog)

from math import fsum,sqrt
from sys import float_info
from bisect import bisect
from time import sleep

import random

MESSAGE_CATEGORY = 'ClusterPoints: Clustering'


class ClusterPointsAlgorithm(QgsProcessingAlgorithm):
    """
    This is an example algorithm that takes a vector layer and
    creates a new identical one.

    It is meant to be used as an example of how to create your own
    algorithms and explain methods and variables used to do it. An
    algorithm like this will be available in all elements, and there
    is not need for additional work.

    All Processing algorithms should extend the QgsProcessingAlgorithm
    class.
    """

    # Constants used to refer to parameters and outputs. They will be
    # used when calling the algorithm from another algorithm, or when
    # calling from the QGIS console.

    Points = 'Points'
    SelectedFeaturesOnly = 'SelectedFeaturesOnly'
    Cluster_Type = 'Cluster_Type'
    RandomSeed = 'RandomSeed'
    Linkage = 'Linkage'
    Distance_Type = 'Distance_Type'
    NumberOfClusters = 'NumberOfClusters'
    AggregationPercentile = 'AggregationPercentile'
    PercentAttrib = 'PercentAttrib'
    AttribValue = 'AttribValue'

    def initAlgorithm(self, config):
        """
        Here we define the inputs and output of the algorithm, along
        with some other properties.
        """

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.Points,
            self.tr('Point layer')))

        self.addParameter(QgsProcessingParameterBoolean(
            self.SelectedFeaturesOnly,
            self.tr('Flag for the use of selected features/points only')))

        self.addParameter(QgsProcessingParameterEnum(
            self.Cluster_Type,
            self.tr("Cluster algorithm (K-Means or Hierarchical)"),
            ['K-Means','Hierarchical'],defaultValue='K-Means'))
  
        self.addParameter(QgsProcessingParameterNumber(
            self.RandomSeed,
            self.tr('RandomSeed for initialization'),
            defaultValue=1,minValue=1,maxValue=999))

        self.addParameter(QgsProcessingParameterEnum(
            self.Linkage,
            self.tr("Link functions for Hierarchical algorithm"),
            ['Single (SLINK)','Single (Lance-Williams)',
            'Complete (Lance-Williams)','Median (Lance-Williams)',
            'Unweighted Average (Lance-Williams)',
            'Ward\'s (Lance-Williams)','Centroid (Lance-Williams)'],
            optional=True))
        
        self.addParameter(QgsProcessingParameterEnum(
            self.Distance_Type,
            self.tr("Distance calculation type"),
            ['Euclidean','Manhattan'],defaultValue='Euclidean'))

        self.addParameter(QgsProcessingParameterNumber(
            self.NumberOfClusters,
            self.tr('User-defined number of clusters'),
            defaultValue=2,minValue=2,maxValue=999))

        self.addParameter(QgsProcessingParameterNumber(
            self.AggregationPercentile,
            self.tr('Cluster feature distance percentile (only used for Lance-Williams)'),
            defaultValue=5,minValue=0,maxValue=99))

        self.addParameter(QgsProcessingParameterNumber(
            self.PercentAttrib,self.tr('Percentage contribution of attribute field'),
            defaultValue=0,minValue=0,maxValue=100))

        self.addParameter(QgsProcessingParameterField(
            self.AttribValue,self.tr('Attribute field'),'',
            self.Points,optional=True))

    def processAlgorithm(self, parameters, context, progress):

        vlayer = self.parameterAsVectorLayer(parameters, self.Points, context)
        SelectedFeaturesOnly = self.parameterAsBool(parameters, self.SelectedFeaturesOnly, context)
        Cluster_Type = self.parameterAsEnum(parameters, self.Cluster_Type, context)
        RandomSeed = self.parameterAsInt(parameters, self.RandomSeed, context)
        Linkage = self.parameterAsEnum(parameters, self.Linkage, context)
        Distance_Type = self.parameterAsEnum(parameters, self.Distance_Type, context)
        NumberOfClusters = self.parameterAsInt(parameters, self.NumberOfClusters, context)
        AggregationPercentile = self.parameterAsInt(parameters, self.AggregationPercentile, context)
        PercentAttrib = self.parameterAsInt(parameters, self.PercentAttrib, context)
        AttribValue = self.parameterAsFields(parameters, self.AttribValue, context)

        links = ["single", "single", "complete", "median", "average", "wards", "centroid"]

        random.seed(RandomSeed)

        provider = vlayer.dataProvider()
        if provider.featureCount()<NumberOfClusters:
            raise QgsProcessingException("Error initializing cluster analysis:\nToo little features available")
        sRs = provider.crs()

        d = QgsDistanceArea()
        d.setSourceCrs(sRs, context.transformContext())
        d.setEllipsoid(context.project().ellipsoid())

        # retrieve input features
        if SelectedFeaturesOnly:
            fit = vlayer.getSelectedFeatures()
        else:
            fit = vlayer.getFeatures()

        # initialize points for clustering
        points = {infeat.id():QgsPoint(infeat.geometry().asPoint()) for \
                  infeat in fit}

        # check on attribute contribution and correct if necessary
        if PercentAttrib>0 and parameters['AttribValue'] is None:
            progress.pushInfo(self.tr("Setting percentage attribute contribution to zero"))
            PercentAttrib = 0

        # retrieve optional z values to consider in clustering
        if PercentAttrib>0:
            id_attr = vlayer.dataProvider().fieldNameIndex(AttribValue[0])
            if id_attr<0:
                raise QgsProcessingException("Field {} not found in input layer".format(AttribValue[0]))
            if not vlayer.fields()[id_attr].typeName().startswith('Int') and \
                  not vlayer.fields()[id_attr].typeName().startswith('Real') and \
                  vlayer.fields()[id_attr].type()!=QVariant.Double:
        	    raise QgsProcessingException("Field {} must be numeric".format(AttribValue[0]))
            if SelectedFeaturesOnly:
                fit = vlayer.getSelectedFeatures()
            else:
                fit = vlayer.getFeatures()
            for infeat in fit:
                if infeat[id_attr] or infeat[id_attr]==0:
                    points[infeat.id()].addZValue(infeat[id_attr])
                else:
                    del points[infeat.id()]
        else:
            for key in points.keys():
                points[key].addZValue()

        if NumberOfClusters>len(points):
            raise QgsProcessingException("Too little valid points "+ \
                                    "available for {} clusters".format(NumberOfClusters))

        # standardize z values with standard deviation of horizontal distances
        if PercentAttrib>0:
            if len(set([p.z() for p in points.values()]))==1:
                raise QgsProcessingException("Field {} must not be constant".format(AttribValue[0])) 
            standard_factor = self.compute_sd_distance([p.x() for p in points.values()], \
                                                       [p.y() for p in points.values()], \
                                                       Distance_Type==1)/ \
                                                       self.__class__.compute_sd( \
                                                       [p.z() for p in points.values()])
            zcenter = fsum([p.z() for p in points.values()])/len(points)
            for key in points.keys():
                points[key].setZ((points[key].z()-zcenter)*standard_factor)

        # define the clustering procedure
        if Cluster_Type==0:
        
            if parameters['Linkage'] is not None:
                progress.pushInfo(self.tr("Linkage not used for K-Means"))
            # K-means clustering
            progress.pushInfo(self.tr("Processing K-Means clustering "+
                                      "with {} points ...".format(len(points))))      
            task = ClusterTask("K-Means clustering", \
                               None,points,PercentAttrib, \
                               NumberOfClusters,d,Distance_Type==1)
                
        else:
        
            # Hierarchical clustering
            if parameters['Linkage'] is None:
                raise QgsProcessingException("Linkage must be Single, "+ \
                                             "Complete, Median, Unweighted Average,"+ \
                                             " Ward\'s or Centroid")
            progress.pushInfo(self.tr("Processing hierarchical clustering "+
                                      "with {} points ...".format(len(points))))      
            if Linkage==0:
                task = ClusterTask("Hierarchical clustering using SLINK", \
                                   links[Linkage],points,PercentAttrib, \
                                   NumberOfClusters,d,Distance_Type==1)             
            else:
                if AggregationPercentile>0:
                    task_add = CFTask("BIRCH-like preprocessing", points,
                                            AggregationPercentile, d=d,
                                            pz=PercentAttrib,
                                            manhattan=(Distance_Type==1))
                    
                    # run potentially expensive preparation in extra task
                    QgsApplication.taskManager().addTask(task_add)
                    
                    while task_add.status()<3:
                        sleep(1)
                        if progress.isCanceled():
                            progress.pushInfo(self.tr("Execution canceled by user"))
                            task_add.cancel()
                            break
                    
                    if progress.isCanceled():
                        cf_data = {}
                    else:
                        cf_data = task_add.return_centroids()
                        if NumberOfClusters>len(cf_data):
                             raise QgsProcessingException("Too little valid cluster features "+ \
                                 "available for {} clusters".format(NumberOfClusters))

                    progress.pushInfo(self.tr("Processing hierarchical clustering "+
                                      "with {} cluster features ...".format(len(cf_data))))                    
                else:
                     cf_data = points
                task = ClusterTask("Hierarchical clustering using "+ \
                                   "Lance-Williams distance updates", \
                                   links[Linkage],cf_data,PercentAttrib, \
                                   NumberOfClusters,d,Distance_Type==1)
        
        # run potentially expensive clustering in extra task
        QgsApplication.taskManager().addTask(task)
        
        while task.status()<3:
            sleep(1)
            if progress.isCanceled():
                progress.pushInfo(self.tr("Execution canceled by user"))
                task.cancel()
                break

        if "Lance-Williams" in task.description() and AggregationPercentile>0:
            task.clusters = [task_add.return_members(cluster) for cluster in task.clusters]
                
        del points

        # assign cluster IDs
        cluster_id = {}
        for idx,cluster in enumerate(task.clusters):
            for key in cluster:
                cluster_id[key] = idx

        progress.pushInfo(self.tr("Writing output field Cluster_ID"))
        
        # prepare output field in input layer
        fieldList = vlayer.dataProvider().fields()
        vlayer.startEditing()
        if "Cluster_ID" in [field.name() for field in fieldList]:
            icl = fieldList.indexFromName("Cluster_ID")
            vlayer.dataProvider().deleteAttributes([icl])
        provider.addAttributes([QgsField("Cluster_ID",QVariant.Int)])
        vlayer.updateFields()
        vlayer.commitChanges()

        # write output field in input layer
        fieldList = vlayer.dataProvider().fields()
        icl = fieldList.indexFromName("Cluster_ID")
        vlayer.startEditing()
        for key in cluster_id.keys():
            vlayer.dataProvider().changeAttributeValues({key:{icl:cluster_id[key]}})
        vlayer.commitChanges()

        progress.setProgress(100)
        
        return {self.Points:"Cluster_ID"}

    def name(self):
        """
        Returns the algorithm name, used for identifying the algorithm. This
        string should be fixed for the algorithm, and must not be localised.
        The name should be unique within each provider. Names should contain
        lowercase alphanumeric characters only and no spaces or other
        formatting characters.
        """
        return 'doCluster'

    def displayName(self):
        """
        Returns the translated algorithm name, which should be used for any
        user-visible display of the algorithm name.
        """
        return self.tr(self.name())

    def group(self):
        """
        Returns the name of the group this algorithm belongs to. This string
        should be localised.
        """
        return self.tr(self.groupId())

    def groupId(self):
        """
        Returns the unique ID of the group this algorithm belongs to. This
        string should be fixed for the algorithm, and must not be localised.
        The group id should be unique within each provider. Group id should
        contain lowercase alphanumeric characters only and no spaces or other
        formatting characters.
        """
        return 'clustering'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return ClusterPointsAlgorithm()

    # Define auxiliary functions
    
    @staticmethod
    def compute_sd(x):
        """
        Computes (unbiased) standard deviation of x
        """
        xmean = fsum(x)/len(x)
        sd = 0
        for i in range(len(x)):
            sd += (x[i]-xmean)*(x[i]-xmean)
        sd = sqrt(sd/(len(x)-1))
        return sd
    
    def compute_sd_distance(self, x, y, manhattan=False):
        """
        Computes standard deviation of distances
        for points describes by x and y 
        (either Euclidean or Manhattan)
        """
        xmean = fsum(x)/len(x)
        ymean = fsum(y)/len(y)
        sd = []
        if manhattan:
            for i in range(len(x)):
                sd.append(x[i]+y[i]-xmean-ymean)
        else:
            for i in range(len(x)):
                sd.append(sqrt((x[i]-xmean)*(x[i]-xmean)+(y[i]-ymean)*(y[i]-ymean)))
        return self.__class__.compute_sd(sd)



# Define task with required functions for each clustering algorithm

class ClusterTask(QgsTask):

    def __init__(self, description, link, points, pz, k, d, manhattan=False):
        super().__init__(description, QgsTask.CanCancel)
        self.link = link
        self.points = points
        self.pz = pz
        self.k = k
        self.d = d
        self.manhattan = manhattan
        self.clusters = []
        self.tree_progress = 0

    def cancel(self):
        QgsMessageLog.logMessage("Cluster task canceled",
            MESSAGE_CATEGORY, Qgis.Critical)
        super().cancel()

    def run(self):
        """
        Execution of task
        """
    
        QgsMessageLog.logMessage(self.description(),MESSAGE_CATEGORY, Qgis.Info)
        if self.description().startswith("K-Means"):
            return self.kmeans()
        elif self.description().startswith("Hierarchical"):
            if "SLINK" in self.description():
                return self.hcluster_slink()
            else:
                return self.hcluster()

    def finished(self,result):
        """
        Called upon finish of execution
        """
        
        if result:
             QgsMessageLog.logMessage(self.tr("Successful execution of clustering task"),
                       MESSAGE_CATEGORY, Qgis.Success)
        else:
             QgsMessageLog.logMessage(self.tr("Execution of clustering task failed"),
                       MESSAGE_CATEGORY, Qgis.Critical)

    def init_kmeans_plusplus(self):
        """
        Initializes the K-means algorithm according to
        Arthur, D. and Vassilvitskii, S. (2007)
        Referred to as K-means++
        """
        
        keys = list(self.points.keys())
        
        # draw first point randomly from dataset with uniform weights
        p = random.choice(keys)
        inits = [KMCluster(set([p]),self.points[p], self.d, self.pz, self.manhattan)]
        
        # loop until k points were found
        while len(inits)<self.k:
            # define new probability weights for sampling
            weights = [min([inits[i].distance2center(self.points[p]) \
                       for i in range(len(inits))]) for p in keys]
            # draw new point randomly with probability weights
            p = random.uniform(0,sum(weights)-float_info.epsilon)
            p = bisect([sum(weights[:i+1]) for i in range(len(weights))],p)
            p = keys[p]
            inits.append(KMCluster(set([p]),self.points[p], self.d, self.pz, self.manhattan))
            
        return inits

    def kmeans(self):

        # Set cut-off distance for termination of iterations
        cutoff=10*float_info.epsilon

        # Create k clusters using the K-means++ initialization method
        QgsMessageLog.logMessage(self.tr(
            "Initializing clusters with K-means++"),
            MESSAGE_CATEGORY, Qgis.Info)
        clusters = self.init_kmeans_plusplus()
        QgsMessageLog.logMessage(self.tr(
            "{} clusters successfully initialized".format(self.k)),
            MESSAGE_CATEGORY, Qgis.Info)
    
        # Loop through the dataset until the clusters stabilize
        loopCounter = 0
        while True:

            if self.isCanceled():
                return False

            # Create a list of lists to hold the points in each cluster
            setList = [set() for i in range(self.k)]
        
            # Start counting loops
            loopCounter += 1

            # For every point in the dataset ...
            for p in list(self.points.keys()):
                # Get the distance between that point and the all the cluster centroids
                smallest_distance = float_info.max
        
                for i in range(self.k):
                    distance = clusters[i].distance2center(self.points[p])
                    if distance < smallest_distance:
                        smallest_distance = distance
                        clusterIndex = i
                setList[clusterIndex].add(p)
        
            # Set biggest_shift to zero for this iteration
            biggest_shift = 0.0
        
            for i in range(self.k):
                # Calculate new centroid coordinates
                numPoints = len(setList[i])
                if numPoints == 0:
                    QgsMessageLog.logMessage(self.tr("Algorithm failed after "+ \
                                             "{} iterations: Choose a ".format(loopCounter)+ \
                                             "different random seed or "+ \
                                             "a smaller number of clusters"),
                                             MESSAGE_CATEGORY, Qgis.Critical)
                    return False
                centerpoint = QgsGeometry.fromPolyline([self.points[p] \
                                         for p in setList[i]]).centroid().asPoint()
                centerpoint = QgsPoint(centerpoint)
                centerpoint.addZValue(sum([self.points[p].z() for p in setList[i]])/len(setList[i]))
                # Calculate how far the centroid moved in this iteration
                shift = clusters[i].update(setList[i], centerpoint)
                # Keep track of the largest move from all cluster centroid updates
                biggest_shift = max(biggest_shift, shift)

            # If the centroids have stopped moving much, say we're done!
            if biggest_shift < cutoff:
                #self.progress.setProgress(90)
                QgsMessageLog.logMessage(self.tr(
                    "Converged after {} iterations").format(loopCounter),
                    MESSAGE_CATEGORY, Qgis.Info)
                break
    
        self.clusters = [c.ids for c in clusters]
        return True

    def hcluster(self):

        clust={}
        distances={}
        currentclustid=-1
        numPoints=len(self.points)
        
        if numPoints==0:
            QgsMessageLog.logMessage(self.tr("No points provided"),
                MESSAGE_CATEGORY, Qgis.Critical)
            return False   

        # clusters are initially singletons
        for ik,p in zip(range(numPoints-1, -1, -1), self.points.keys()):
            clust[ik] = Cluster_node(members=[p],d=self.d,pz=self.pz,manhattan=self.manhattan)
        
        # compute pairwise distances
        for ik in clust.keys():
            if self.isCanceled():
                return False
            for jk in clust.keys():
                if jk<ik:
                    distances[(ik,jk)]=clust[ik].getDistance(self.points[clust[ik].members[0]], \
                                       self.points[clust[jk].members[0]])

        while currentclustid>=self.k-numPoints:
        
            if self.isCanceled():
                return False
        
            closest = float_info.max
    
            # loop through every pair looking for the smallest distance
            for ik in clust.keys():
                for jk in clust.keys():
                    if jk<ik:
                        dist=distances[(ik,jk)]
                        if dist<closest:
                            closest=dist
                            ik_lowest=ik
                            jk_lowest=jk
            
            # detect clusters to merge
            ik = ik_lowest
            jk = jk_lowest
            
            # create the new cluster
            clust[currentclustid]=Cluster_node(members=clust[ik].members+ \
                                  clust[jk].members,d=self.d,pz=self.pz)
                                  
            # compute updated distances according to the Lance-Williams algorithm
            
            if self.link == 'single':

                alpha_i = 0.5
                alpha_j = 0.5
                gamma = -0.5
                for lk in clust.keys():
                    if lk not in (ik,jk,currentclustid):
                        jl = (jk,lk) if jk>lk else (lk,jk)
                        il = (ik,lk) if ik>lk else (lk,ik)
                        distances[(lk,currentclustid)] = alpha_i*distances[il]+ \
                                  alpha_j*distances[jl]+gamma*abs(distances[il]-distances[jl])

            elif self.link == 'complete':
            
                alpha_i = 0.5
                alpha_j = 0.5
                gamma = 0.5
                for lk in clust.keys():
                    if lk not in (ik,jk,currentclustid):
                        jl = (jk,lk) if jk>lk else (lk,jk)
                        il = (ik,lk) if ik>lk else (lk,ik)
                        distances[(lk,currentclustid)] = alpha_i*distances[il]+ \
                                  alpha_j*distances[jl]+gamma*abs(distances[il]-distances[jl])

            elif self.link == 'median':
            
                alpha_i = 0.5
                alpha_j = 0.5
                beta = -0.25
                for lk in clust.keys():
                    if lk not in (ik,jk,currentclustid):
                        jl = (jk,lk) if jk>lk else (lk,jk)
                        il = (ik,lk) if ik>lk else (lk,ik)
                        distances[(lk,currentclustid)] = alpha_i*distances[il]+ \
                                  alpha_j*distances[jl]+beta*distances[(ik,jk)]
  
            elif self.link == 'average':
            
                alpha_i = float(clust[ik].size)/clust[currentclustid].size
                alpha_j = float(clust[jk].size)/clust[currentclustid].size
                for lk in clust.keys():
                    if lk not in (ik,jk,currentclustid):
                        jl = (jk,lk) if jk>lk else (lk,jk)
                        il = (ik,lk) if ik>lk else (lk,ik)
                        distances[(lk,currentclustid)] = \
                                  alpha_i*distances[il]+alpha_j*distances[jl]

            elif self.link == 'wards':
            
                for lk in clust.keys():
                    if lk not in (ik,jk,currentclustid):
                        alpha_i = float(clust[ik].size+clust[lk].size)/ \
                                  (clust[currentclustid].size+clust[lk].size)
                        alpha_j = float(clust[jk].size+clust[lk].size)/ \
                                  (clust[currentclustid].size+clust[lk].size)
                        beta = -float(clust[lk].size)/ \
                                (clust[currentclustid].size+clust[lk].size)
                        jl = (jk,lk) if jk>lk else (lk,jk)
                        il = (ik,lk) if ik>lk else (lk,ik)
                        distances[(lk,currentclustid)] = alpha_i*distances[il]+ \
                                  alpha_j*distances[jl]+beta*distances[(ik,jk)]

            elif self.link == 'centroid':
            
                for lk in clust.keys():
                    if lk not in (ik,jk,currentclustid):
                        alpha_i = float(clust[ik].size)/clust[currentclustid].size
                        alpha_j = float(clust[jk].size)/clust[currentclustid].size
                        beta = -float(clust[ik].size*clust[jk].size)/ \
                                (clust[currentclustid].size**2)
                        jl = (jk,lk) if jk>lk else (lk,jk)
                        il = (ik,lk) if ik>lk else (lk,ik)
                        distances[(lk,currentclustid)] = alpha_i*distances[il]+ \
                                  alpha_j*distances[jl]+beta*distances[(ik,jk)]

            else:

                 QgsMessageLog.logMessage(self.tr(
                     "Link function invalid/not found"),
                     MESSAGE_CATEGORY, Qgis.Critical)
                 super().cancel()

            # delete deprecated clusters
            del clust[ik]
            del clust[jk]
            
            # display progress only at intervals of 5%
            tree_progress = int(20*currentclustid/(self.k-numPoints))
            if tree_progress > self.tree_progress:
                self.tree_progress = tree_progress
                QgsMessageLog.logMessage(self.tr("{}% of cluster tree built".format( \
                                                 5*tree_progress)),MESSAGE_CATEGORY,
                                                 Qgis.Info)

            # cluster ids that weren't in the original set are negative
            currentclustid-=1

        QgsMessageLog.logMessage(self.tr("Cluster tree fully computed"),
            MESSAGE_CATEGORY, Qgis.Info)

        self.clusters = [c.members for c in list(clust.values())]
        return True

    def hcluster_slink(self):

        def findClusterMembers(Pi,keys,ik,clusters):
            members = []
            for i in (i for i,jk in enumerate(Pi) if jk==ik):
                if keys[i] not in [x for y in clusters for x in y]:
                    members.append(keys[i])
                members += findClusterMembers(Pi,keys,i,clusters)
            return members

        numPoints = len(self.points)
        keys = list(self.points.keys())
        Pi = [None]*numPoints
        Lambda = [None]*numPoints
        M = [None]*numPoints
        iks = []
        clusters = []
        
        # Initialize SLINK algorithm
        Pi[0] = 0
        Lambda[0] = float_info.max
        cluster_sample=Cluster_node(d=self.d,pz=self.pz,manhattan=self.manhattan)
        
        # Iterate over vertices (called OTUs)
        for i in range(1,numPoints):
        
            if self.isCanceled():
                return False
        
            Pi[i] = i
            Lambda[i] = float_info.max
            M[:i] = [cluster_sample.getDistance(self.points[keys[p]],self.points[keys[i]]) \
                     for p in range(i)]            
            for p in range(i):
                if Lambda[p]>=M[p]:
                    M[Pi[p]] = min(M[Pi[p]],Lambda[p])
                    Lambda[p] = M[p]
                    Pi[p] = i
                else:
                    M[Pi[p]] = min(M[Pi[p]],M[p])
            Pi[:i] = [x if Lambda[x]>Lambda[p] else i for p,x in enumerate(Pi[:i])]
            
            # display progress only at intervals of 5%
            tree_progress = int(20*i/numPoints)
            if tree_progress > self.tree_progress:
                self.tree_progress = tree_progress
                QgsMessageLog.logMessage(self.tr("{}% of cluster tree built".format( \
                                                 5*tree_progress)),MESSAGE_CATEGORY,
                                                 Qgis.Info)

        # Identify clusters in pointer representation
        for clusterIndex in range(1,self.k):
            closest = float_info.min
            
            for p in range(numPoints-1):
                if Lambda[p]>closest:
                    ik = p
                    closest = Lambda[p]
            Lambda[ik] = float_info.min
            iks.append(ik)

        iks.reverse()
        
        for ik in iks:
            clusters.append([keys[ik]]+findClusterMembers(Pi,keys,ik,clusters))
            
        # assign remaining points to the last cluster
        clusters.append([p for p in keys if p not in [x for y in clusters for x in y]])

        #self.progress.setProgress(90)
        QgsMessageLog.logMessage(self.tr("Cluster tree fully computed"),
            MESSAGE_CATEGORY, Qgis.Info)

        self.clusters = clusters
        return True



# Define required cluster classes

class KMCluster:
    '''
    Class for k-means clustering
    '''
    def __init__(self, ids, centerpoint, d, pz=0, manhattan=False):
        '''
        ids - set of integer IDs of the cluster points
        centerpoint - point of centroid
        d - distance calculation reference
        pz - percentage contribution of the z coordinate
        '''
        
        if len(ids) == 0: raise Exception("Error: Empty cluster")
        
        # The point IDs that belong to this cluster
        self.ids = ids
        
        # The center that belongs to this cluster
        self.centerpoint = centerpoint

        # Initialize distance computing
        self.d = d
        
        # The percentage contribution of the z value
        self.pz = pz
        
        # Whether to use the Manhattan distance 
        self.manhattan = manhattan
    
    def update(self, ids, centerpoint):
        '''
        Returns the distance between the previous centroid coordinates
        and the new centroid coordinates 
        and updates the point IDs and the centroid coordinates
        '''
        old_centerpoint = self.centerpoint
        self.ids = ids
        self.centerpoint = centerpoint
        return self.distance2center(old_centerpoint)
    
    def distance2center(self, point):
        '''
        "2-dimensional Euclidean distance or Manhattan distance to centerpoint
        plus percentage contribution (pz) of z value.
        '''
        if self.manhattan:
            return (1-0.01*self.pz)* \
                (self.d.measureLine(QgsPointXY(self.centerpoint), \
                QgsPointXY(point.x(),self.centerpoint.y()))+ \
                self.d.measureLine(QgsPointXY(self.centerpoint), \
                QgsPointXY(self.centerpoint.x(),point.y()))+ \
                self.d.measureLine(QgsPointXY(point), \
                QgsPointXY(point.x(),self.centerpoint.y()))+ \
                self.d.measureLine(QgsPointXY(point), \
                QgsPointXY(self.centerpoint.x(),point.y())))+ \
                2*0.01*self.pz*abs(point.z()-self.centerpoint.z())
        else:
            return (1-0.01*self.pz)* \
                self.d.measureLine(QgsPointXY(self.centerpoint),QgsPointXY(point))+ \
                0.01*self.pz*abs(point.z()-self.centerpoint.z())
                
class Cluster_node:
    '''
    Class for hierarchical clustering
    '''
    def __init__(self, members=[], d=None, pz=0, manhattan=False):
        self.members = members
        self.size = len(members)
        self.d = d
        self.pz = pz
        self.manhattan = manhattan

    def getDistance(self, point1, point2):
        '''
        2-dimensional Euclidean distance or Manhattan distance between points 1 and 2
        plus percentage contribution (pz) of z value.
        '''
        if self.manhattan:
            return (1-0.01*self.pz)*(self.d.measureLine(QgsPointXY(point1), \
                QgsPointXY(point2.x(),point1.y()))+ \
                self.d.measureLine(QgsPointXY(point1), \
                QgsPointXY(point1.x(),point2.y()))+ \
                self.d.measureLine(QgsPointXY(point2), \
                QgsPointXY(point2.x(),point1.y()))+ \
                self.d.measureLine(QgsPointXY(point2), \
                QgsPointXY(point1.x(),point2.y())))+ \
                2*0.01*self.pz*abs(point1.z()-point2.z())
        else:
            return (1-0.01*self.pz)* \
                self.d.measureLine(QgsPointXY(point1),QgsPointXY(point2))+ \
                0.01*self.pz*abs(point1.z()-point2.z())
