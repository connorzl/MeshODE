import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + 'layers')
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'build')))

from torch import nn
import torch.optim as optim
from torch.autograd import Function

import torch
from layers.graph_loss2_layer import GraphLoss2LayerMulti, Finalize
from layers.reverse_loss_layer import ReverseLossLayer
from layers.maf import MAF
from layers.neuralode_fast import NeuralODE
from layers.pointnet import PointNetfeat, feature_transform_regularizer
import pyDeform

import numpy as np
from time import time

import argparse

parser = argparse.ArgumentParser(description='Rigid Deformation.')
parser.add_argument('--source', default='../data/cad-source.obj')
parser.add_argument('--target', default=[], action='append')
parser.add_argument('--output', default='./cad-output')
parser.add_argument('--rigidity', default='0.1')
parser.add_argument('--device', default='cuda')
parser.add_argument('--save_path', default='./cad-output.ckpt')

args = parser.parse_args()

source_path = args.source
reference_paths = args.target
output_path = args.output
rigidity = float(args.rigidity)
save_path = args.save_path
device = torch.device(args.device)

FEATURES_REG_LOSS_WEIGHT = 0.001

# Vertices has shape [V, 3], each element is (v_x, v_y, v_z)
# Faces has shape [F, 3], each element is (i, j, k)
# Edges has shape [E, 2], each element is (i, j)
# V2G1 maps each vertex to the voxel number that it belongs to, has shape [F, 1]
# Skeleton mesh vertices has shape [GV, 3], each skeleton vertex is the average of vertices in its voxel.
# Skeleton mesh edges has shape [GE, 2]
V1, F1, E1, V2G1, GV1, GE1 = pyDeform.LoadCadMesh(source_path)

V_targs = []
F_targs = []
E_targs = []
V2G_targs = []
GV_targs = []
GE_targs = []
n_targs = len(reference_paths)
for reference_path in reference_paths:
    V, F, E, V2G, GV, GE = pyDeform.LoadCadMesh(reference_path)
    V_targs.append(V)
    F_targs.append(F)
    E_targs.append(E)
    V2G_targs.append(V2G)
    GV_targs.append(GV)
    GE_targs.append(GE)

# PointNet layer.
pointnet = PointNetfeat(global_feat=True, feature_transform=True)
pointnet = pointnet.to(device)
pointnet.eval()

# Deformation losses layer.
graph_loss = GraphLoss2LayerMulti(
    V1, F1, GV1, GE1, V_targs, F_targs, GV_targs, GE_targs, rigidity, device)
param_id1 = graph_loss.param_id1
param_id_targs = graph_loss.param_id_targs

reverse_loss = ReverseLossLayer()

# Flow layer.
func = NeuralODE(device, use_pointnet=True)

optimizer = optim.Adam(func.parameters(), lr=1e-3)

# Prepare input for encoding the skeleton meshes, shape = [1, 3, n_pts].
GV1_pointnet_input = GV1.unsqueeze(0)
GV1_pointnet_input = GV1_pointnet_input.transpose(2, 1).to(device)

GV_pointnet_input_targs = []
for GV_targ in GV_targs:
    GV_pointnet_input = GV_targ.unsqueeze(0)
    GV_pointnet_input = GV_pointnet_input.transpose(2, 1).to(device)
    GV_pointnet_input_targs.append(GV_pointnet_input)

# Clone skeleton vertices for computing loss.
GV1_origin = GV1.clone()
GV_origin_targs = []
for GV_targ in GV_targs:
    GV_origin = GV_targ.clone()
    GV_origin_targs.append(GV_origin)

# Move skeleton vertices to device for deformation.
GV1_device = GV1.to(device)
GV_device_targs = []
for GV in GV_targs:
    GV_targ = GV.to(device)
    GV_device_targs.append(GV_targ)

niter = 1000
loss_min = 1e30
eps = 1e-6
print("Starting training!")
for it in range(0, niter):
    optimizer.zero_grad()

    # Encode source skeleton mesh.
    GV1_features_device, _, GV1_trans_feat = pointnet(GV1_pointnet_input)
    GV1_features_device = torch.squeeze(GV1_features_device)

    loss = 0
    for i in range(n_targs):
        # Encode each target skeleton mesh.
        GV2_features_device, _, GV2_trans_feat = pointnet(GV_pointnet_input_targs[i])
        GV2_features_device = torch.squeeze(GV2_features_device)

        # Compute and integrate velocity field for deformation.
        GV1_deformed, GV2_features_deformed = func.forward(
            (GV1_device, GV2_features_device))
        # The target features should stay the same, since they are only used to integrate GV1.
        if torch.norm(GV2_features_device - GV2_features_deformed) > eps:
            raise ValueError("Non-identity target features deformation.")

        # Same as above, but in opposite direction.
        GV2_deformed, GV1_features_deformed = func.inverse(
            (GV_device_targs[i], GV1_features_device))
        if torch.norm(GV1_features_device - GV1_features_deformed) > eps:
            raise ValueError("Non-identity target features deformation.")
    
        # Source to target.
        loss1_forward = graph_loss(
            GV1_deformed, GE1, GV_targs[i], GE_targs[i], i, 0)
        loss1_backward = reverse_loss(GV1_deformed, GV_origin_targs[i], device)
        loss1_features_reg = FEATURES_REG_LOSS_WEIGHT * \
            feature_transform_regularizer(GV1_trans_feat)

        # Target to source.
        loss2_forward = graph_loss(GV1, GE1, GV2_deformed, GE_targs[i], i, 1)
        loss2_backward = reverse_loss(GV2_deformed, GV1_origin, device)
        loss2_features_reg = FEATURES_REG_LOSS_WEIGHT * \
            feature_transform_regularizer(GV2_trans_feat)

        # Total loss.
        loss = loss1_forward + loss1_backward + loss2_forward + \
            loss1_features_reg + loss2_backward + loss2_features_reg

        if it % 100 == 0 or True:
            print('iter=%d, target_index=%d loss1_forward=%.6f loss1_backward=%.6f loss2_forward=%.6f loss2_backward=%.6f'
                  % (it, i, np.sqrt(loss1_forward.item() / GV1.shape[0]),
                     np.sqrt(loss1_backward.item() / GV_targs[i].shape[0]),
                     np.sqrt(loss2_forward.item() / GV_targs[i].shape[0]),
                     np.sqrt(loss2_backward.item() / GV1.shape[0])))

    loss.backward()
    optimizer.step()

    current_loss = loss.item()

# Evaluate final result.
if save_path != '':
    torch.save({'func': func, 'optim': optimizer}, save_path)

V1_copy_skeleton = V1.clone()
V1_copy_direct = V1.clone() 
V1_copy_direct_origin = V1_copy_direct.clone()

skeleton_output_path = os.path.join(os.path.dirname(output_path), os.path.basename(output_path) + "_skeleton.obj")
direct_output_path = os.path.join(os.path.dirname(output_path), os.path.basename(output_path) + "_direct.obj")

# Deform skeleton mesh, then apply to original mesh.
GV2_features_device, _, _ = pointnet(GV_pointnet_input_targs[-1])
GV2_features_device = torch.squeeze(GV2_features_device)
GV1_deformed, _ = func.forward((GV1_device, GV2_features_device))
GV1_deformed = torch.from_numpy(GV1_deformed.data.cpu().numpy())
Finalize(V1_copy_skeleton, F1, E1, V2G_targs[-1], GV1_deformed, rigidity, param_id_targs[-1])
pyDeform.SaveMesh(skeleton_output_path, V1_copy_skeleton, F1)

# Deform original mesh directly, different from paper.
pyDeform.NormalizeByTemplate(V1_copy_direct, param_id1.tolist())

func.func = func.func.cpu()
# Considering extracting features for the original target mesh here.
V1_copy_direct, _ = func.forward((V1_copy_direct, GV2_features_device.cpu()))
V1_copy_direct = torch.from_numpy(V1_copy_direct.data.cpu().numpy())

src_to_src = torch.from_numpy(
    np.array([i for i in range(V1_copy_direct_origin.shape[0])]).astype('int32'))

pyDeform.SolveLinear(V1_copy_direct_origin, F1, E1, src_to_src, V1_copy_direct, 1, 1)
pyDeform.DenormalizeByTemplate(V1_copy_direct_origin, param_id_targs[-1].tolist())
pyDeform.SaveMesh(direct_output_path, V1_copy_direct_origin, F1)