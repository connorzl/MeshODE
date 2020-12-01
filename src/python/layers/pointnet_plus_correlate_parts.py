import os
import sys
import numpy as np
import torch
import torch.nn.functional as F
from torch import nn
import pytorch_lightning as pl
from pointnet2_ops.pointnet2_modules import PointnetSAModule


class PointNetCorrelateParts(pl.LightningModule):
    def __init__(self, num_parts, input_latent_size, output_latent_size):
        super().__init__()
        self.num_parts = num_parts
        self.input_latent_size = input_latent_size
        self.output_latent_size = output_latent_size
        self.bn = False
        self._build_model()

    def _build_model(self):
        self.SA_modules = nn.ModuleList()
        self.SA_modules.append(
            PointnetSAModule(
                npoint=512,
                radius=0.2,
                nsample=64,
                mlp=[self.input_latent_size, 64, 64, 128],
                bn=self.bn,
                use_xyz=True,
            )
        )
        self.SA_modules.append(
            PointnetSAModule(
                npoint=128,
                radius=0.4,
                nsample=64,
                mlp=[128, 128, 128, 256],
                bn=self.bn,
                use_xyz=True,
            )
        )
        self.SA_modules.append(
            PointnetSAModule(
                mlp=[256, 256, 512, 1024], 
                bn=self.bn,
                use_xyz=True,
            )
        )

        self.fc_layer = nn.Sequential(
            nn.Linear(1024, 512, bias=False),
            nn.LeakyReLU(True),
            nn.Linear(512, 256, bias=False),
            nn.LeakyReLU(True),
            nn.Dropout(0.5),
            nn.Linear(256, self.num_parts * self.output_latent_size)
        )

    def _break_up_pc(self, pc):
        xyz = pc[..., 0:3].contiguous()
        features = pc[..., 3:].transpose(1, 2).contiguous() if pc.size(-1) > 3 else None

        return xyz, features

    def forward(self, pointcloud):
        r"""
            Forward pass of the network

            Parameters
            ----------
            pointcloud: Variable(torch.cuda.FloatTensor)
                (B, N, 3 + input_channels) tensor
                Point cloud to run predicts on
                Each point in the point-cloud MUST
                be formated as (x, y, z, features...)
        """
        xyz, features = self._break_up_pc(pointcloud)

        for module in self.SA_modules:
            xyz, features = module(xyz, features)
        res = self.fc_layer(features.squeeze(-1))
        return res

